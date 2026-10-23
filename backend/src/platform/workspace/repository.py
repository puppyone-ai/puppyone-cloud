"""Durable computer ownership. All mutations go through fenced SQL commands."""

from src.infra.supabase.dependencies import get_supabase_client


class WorkbenchRepository:
    def __init__(self, client=None):
        self.client = client if client is not None else get_supabase_client()

    def command(self, name, **values):
        return (
            self.client.rpc(
                "cloud_workbench_" + name, {"p_" + key: value for key, value in values.items()}
            )
            .execute()
            .data
        )

    def context(self, user, project):
        return self.command("context", user=user, project=project)

    def default(self, user, project, revision):
        return self.command("default", user=user, project=project, revision=revision)

    def get(self, identity):
        rows = (
            self.client.table("cloud_workbenches")
            .select("*")
            .eq("id", identity)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None

    def acquire(self, row, binding):
        return self.command(
            "acquire",
            workbench=row["id"],
            user=row["user_id"],
            project=row["project_id"],
            binding=binding,
        )

    def transition(self, row, state, resource=None):
        return self.command(
            "transition",
            workbench=row["id"],
            generation=row["generation"],
            version=row["version"],
            operation=row["operation_id"],
            state=state,
            resource=resource,
        )

    def activity(self, row, revision, *, write=False):
        return self.command(
            "activity",
            workbench=row["id"],
            generation=row["generation"],
            user=row["user_id"],
            project=row["project_id"],
            revision=revision,
            write=write,
        )

    def request_open(self, row, binding, revision):
        return self.command(
            "request_open",
            workbench=row["id"],
            user=row["user_id"],
            project=row["project_id"],
            binding=binding,
            revision=revision,
        )

    def pending(self, provider):
        return self.command("pending", provider=provider)

    def progress(self, row, phase, error=None, retryable=False):
        return self.command(
            "progress",
            workbench=row["id"],
            generation=row["generation"],
            operation=row["operation_id"],
            phase=phase,
            error=error,
            retryable=retryable,
        )

    def ready(self, row, resource):
        return self.command(
            "ready",
            workbench=row["id"],
            generation=row["generation"],
            version=row["version"],
            operation=row["operation_id"],
            resource=resource,
        )

    def finish_open(self, row, revision):
        return self.command(
            "finish_open",
            workbench=row["id"],
            generation=row["generation"],
            version=row["version"],
            operation=row["operation_id"],
            epoch=row["compute_epoch"],
            resource=row["resource"],
            revision=revision,
        )

    def verify_opening_contract(self):
        """Probe the exact RPC signature with a guaranteed rejected, absent identity."""
        try:
            self.command(
                "finish_open",
                workbench=None,
                generation=None,
                version=None,
                operation=None,
                epoch=None,
                resource=None,
                revision=None,
            )
        except Exception as exc:
            if (
                getattr(exc, "code", None) == "P0001"
                and getattr(exc, "message", None) == "agent_context_changed"
            ):
                return
            raise
        raise RuntimeError("Workspace opening contract did not reject an absent identity")

    def expire_open(self, row):
        return self.command("expire_open", workbench=row["id"], deadline=row["open_deadline"])

    def due(self, provider=None):
        return (
            self.command("maintenance_for_provider", provider=provider)
            if provider
            else self.command("maintenance")
        )

    def idle(self, row):
        return self.command(
            "idle_claim",
            workbench=row["id"],
            generation=row["generation"],
            version=row["version"],
            action="pause",
        )

    def lifecycle_claim(self, row, *, suspend=False):
        return self.command(
            "suspend" if suspend else "reconcile_claim",
            workbench=row["id"],
            generation=row["generation"],
            version=row["version"],
        )

    def audit(self, user, project, generation, operation, identity, status):
        # Intent precedes native mutation. An intent without completion means
        # unknown outcome; neither an exception nor a dropped HTTP reply is undo.
        self.client.table("audit_logs").insert(
            {
                "action": "workbench." + operation["action"],
                "path": operation["path"],
                "project_id": project,
                "operator_type": "user",
                "operator_id": user,
                "status": status,
                "source_channel": "workbench",
                "metadata": {
                    "operation_id": identity,
                    "generation": generation,
                    "destination": operation.get("destination"),
                    "expected_revision": operation.get("expected_revision"),
                },
            }
        ).execute()
