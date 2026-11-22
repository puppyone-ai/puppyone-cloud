"""Entitlement snapshot repositories shared by the entitlement tests."""

from __future__ import annotations

from datetime import UTC, datetime

from src.platform.entitlements.models import EntitlementSnapshot


class _EmptyEntitlementRepository:
    def get_by_org_id(self, org_id: str):
        return None


class _ValidEntitlementRepository:
    def get_by_org_id(self, org_id: str):
        return EntitlementSnapshot(
            org_id=org_id,
            plan_id="free",
            status="free",
            source="puppypay",
            seat_quantity=1,
            catalog_version="launch-v1",
            source_revision=1,
            effective_at=datetime(2026, 7, 14, tzinfo=UTC),
            payload_hash="a" * 64,
            entitlements={
                "features": {
                    "access_surface.agent": False,
                    "access_surface.mcp": False,
                    "access_surface.sandbox": False,
                    "automation.hosted": False,
                    "remote_workspace.create": False,
                    "scope_sandbox.connect": False,
                },
                "limits": {
                    "projects.max": 1,
                    "repo_scopes.max_per_project": 2,
                    "storage.max_bytes": 500 * 1024**2,
                    "upload.max_single_file_bytes": 50 * 1024**2,
                    "seats.purchased": 1,
                    "runtime.included_units": 0,
                },
                "allow": {"access_surface_kinds": ["git_remote", "cli", "direct"]},
            },
        )
