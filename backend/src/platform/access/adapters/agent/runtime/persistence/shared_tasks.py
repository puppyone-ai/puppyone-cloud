"""Service-only task ledger adapter; no provider operations or table mutations."""

from src.infra.supabase.dependencies import get_supabase_client


class SharedTaskRepository:
    def __init__(self, client=None):
        self.client = client if client is not None else get_supabase_client()

    def workbench(self, identity):
        rows = self.client.table("cloud_workbenches").select("*").eq("id", identity).limit(1).execute().data
        return rows[0] if rows else None

    def active(self, run):
        # A fenced supervisor must never discover/stop its successor's task.
        return (self.client.table("cloud_workbench_tasks").select("*")
                .eq("workbench_id", run["workbench_id"]).eq("run_id", run["id"])
                .eq("state", "active").lte("fence", run["fence"])
                .order("fence").execute().data)

    def _command(self, name, run, **values):
        return self.client.rpc("cloud_workbench_task_" + name, {
            "p_run": run["id"], "p_execution": run["execution_id"], "p_fence": run["fence"],
            **{"p_" + key: value for key, value in values.items()},
        }).execute().data

    def acquire(self, run, generation):
        return self._command("acquire", run, generation=generation)

    def released(self, run, task):
        return self._command("released", run, task=task["execution_id"], generation=task["generation"])
