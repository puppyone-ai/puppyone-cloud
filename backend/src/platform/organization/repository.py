import secrets
from datetime import UTC, datetime, timedelta

from src.platform.organization.models import Organization, OrgInvitation, OrgMember
from src.utils.id_generator import generate_uuid_v7


class OrganizationRepository:
    def __init__(self, supabase_client=None):
        if supabase_client is None:
            from src.infra.supabase.dependencies import get_supabase_client

            self._client = get_supabase_client()
        else:
            self._client = supabase_client

    # ── Organization CRUD ──

    def get_by_id(self, org_id: str) -> Organization | None:
        resp = self._client.table("organizations").select("*").eq("id", org_id).execute()
        if resp.data:
            return Organization(**resp.data[0])
        return None

    def get_by_slug(self, slug: str) -> Organization | None:
        resp = self._client.table("organizations").select("*").eq("slug", slug).execute()
        if resp.data:
            return Organization(**resp.data[0])
        return None

    def list_by_user(self, user_id: str) -> list[Organization]:
        resp = (
            self._client.table("org_members")
            .select("org_id, organizations(*)")
            .eq("user_id", user_id)
            .execute()
        )
        orgs = []
        for row in resp.data:
            if row.get("organizations"):
                orgs.append(Organization(**row["organizations"]))
        return orgs

    def create(self, name: str, slug: str, created_by: str) -> Organization:
        data = {
            "id": generate_uuid_v7(),
            "name": name,
            "slug": slug,
            "type": "team",
            "created_by": created_by,
        }
        resp = self._client.table("organizations").insert(data).execute()
        return Organization(**resp.data[0])

    def update(self, org_id: str, **kwargs) -> Organization | None:
        kwargs["updated_at"] = datetime.now(UTC).isoformat()
        resp = self._client.table("organizations").update(kwargs).eq("id", org_id).execute()
        if resp.data:
            return Organization(**resp.data[0])
        return None

    def delete_empty_control_plane(self, org_id: str, actor_user_id: str) -> dict:
        """Atomically delete an Organization only after proving it has no Projects.

        Project rows must be removed through the Project deletion control plane,
        which durably records object cleanup before deleting relational state.
        A direct Organizations DELETE would let the FK cascade bypass that
        journal, so production code has no direct-delete repository primitive.
        """

        response = self._client.rpc(
            "delete_empty_organization_control_plane",
            {
                "p_org_id": org_id,
                "p_actor_user_id": actor_user_id,
            },
        ).execute()
        data = response.data
        if isinstance(data, list):
            data = data[0] if data else None
        return data if isinstance(data, dict) else {"outcome": "invalid_response"}

    # ── Members ──

    def get_member(self, org_id: str, user_id: str) -> OrgMember | None:
        resp = (
            self._client.table("org_members")
            .select("*")
            .eq("org_id", org_id)
            .eq("user_id", user_id)
            .execute()
        )
        if resp.data:
            return OrgMember(**resp.data[0])
        return None

    def get_owner_user_id(self, org_id: str) -> str | None:
        resp = (
            self._client.table("org_members")
            .select("user_id")
            .eq("org_id", org_id)
            .eq("role", "owner")
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return str(rows[0]["user_id"]) if rows else None

    def list_members(self, org_id: str) -> list[dict]:
        """List members with profile info via PostgREST join (FK: org_members.user_id → profiles.user_id)."""
        try:
            resp = (
                self._client.table("org_members")
                .select("*, profiles(email, display_name, avatar_url)")
                .eq("org_id", org_id)
                .execute()
            )
            return resp.data
        except Exception:
            # Fallback: two-step query if FK not yet migrated
            resp = self._client.table("org_members").select("*").eq("org_id", org_id).execute()
            members = resp.data
            if not members:
                return members

            user_ids = [m["user_id"] for m in members]
            profiles_resp = (
                self._client.table("profiles")
                .select("user_id, email, display_name, avatar_url")
                .in_("user_id", user_ids)
                .execute()
            )
            profile_map = {p["user_id"]: p for p in profiles_resp.data}
            for m in members:
                m["profiles"] = profile_map.get(m["user_id"], {})
            return members

    def add_member(self, org_id: str, user_id: str, role: str = "member") -> OrgMember:
        data = {
            "id": generate_uuid_v7(),
            "org_id": org_id,
            "user_id": user_id,
            "role": role,
        }
        resp = self._client.table("org_members").insert(data).execute()
        return OrgMember(**resp.data[0])

    def update_member_role(self, org_id: str, user_id: str, role: str) -> OrgMember | None:
        resp = (
            self._client.table("org_members")
            .update({"role": role})
            .eq("org_id", org_id)
            .eq("user_id", user_id)
            .execute()
        )
        if resp.data:
            return OrgMember(**resp.data[0])
        return None

    def transfer_ownership(
        self,
        org_id: str,
        current_owner_user_id: str,
        new_owner_user_id: str,
    ) -> bool:
        response = self._client.rpc(
            "transfer_organization_ownership",
            {
                "p_org_id": org_id,
                "p_current_owner": current_owner_user_id,
                "p_new_owner": new_owner_user_id,
            },
        ).execute()
        return response.data is True

    def remove_member(self, org_id: str, user_id: str) -> bool:
        resp = (
            self._client.table("org_members")
            .delete()
            .eq("org_id", org_id)
            .eq("user_id", user_id)
            .execute()
        )
        return len(resp.data) > 0

    def count_members(self, org_id: str) -> int:
        resp = (
            self._client.table("org_members")
            .select("id", count="exact")
            .eq("org_id", org_id)
            .execute()
        )
        return resp.count or 0

    def count_billable_members(self, org_id: str) -> int:
        try:
            data = (
                self._client.rpc(
                    "count_billable_organization_members",
                    {"p_org_id": org_id},
                )
                .execute()
                .data
            )
            if isinstance(data, list):
                data = data[0] if data else 0
            return int(data or 0)
        except Exception:
            # Additive rollout compatibility is allowed only while seat billing
            # is disabled. Hosted shadow/required deployments must use the
            # capability-derived database policy and therefore fail closed.
            from src.config import settings

            if settings.SEAT_BILLING_MODE != "disabled":
                raise
            resp = (
                self._client.table("org_members")
                .select("id", count="exact")
                .eq("org_id", org_id)
                .in_("role", ["owner", "member"])
                .execute()
            )
            return resp.count or 0

    # ── Invitations ──

    def create_invitation(
        self, org_id: str, email: str, role: str, invited_by: str
    ) -> OrgInvitation:
        data = {
            "id": generate_uuid_v7(),
            "org_id": org_id,
            "email": email,
            "role": role,
            "invited_by": invited_by,
            "token": secrets.token_urlsafe(32),
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        }
        resp = self._client.table("org_invitations").insert(data).execute()
        return OrgInvitation(**resp.data[0])

    def get_invitation_by_token(self, token: str) -> OrgInvitation | None:
        resp = (
            self._client.table("org_invitations")
            .select("*")
            .eq("token", token)
            .eq("status", "pending")
            .execute()
        )
        if resp.data:
            return OrgInvitation(**resp.data[0])
        return None

    def accept_invitation(self, invitation_id: str) -> None:
        self._client.table("org_invitations").update({"status": "accepted"}).eq(
            "id", invitation_id
        ).execute()

    def revoke_invitation(self, org_id: str, invitation_id: str) -> None:
        """Mark a pending invitation as revoked. Idempotent: scoped by
        org_id so a cross-org call cannot revoke someone else's invite,
        and matched only against ``status='pending'`` so re-revoking an
        already-revoked / accepted row is a no-op (not an error)."""
        self._client.table("org_invitations").update({"status": "revoked"}).eq(
            "id", invitation_id
        ).eq("org_id", org_id).eq("status", "pending").execute()

    def list_invitations(self, org_id: str) -> list[OrgInvitation]:
        resp = (
            self._client.table("org_invitations")
            .select("*")
            .eq("org_id", org_id)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .execute()
        )
        return [OrgInvitation(**row) for row in resp.data]
