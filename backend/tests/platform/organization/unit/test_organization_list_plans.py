"""The organization list says which plan each space is on, so the Homepage can
label each group of projects with the plan its projects use."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from src.platform.auth.models import CurrentUser
from src.platform.organization import router as organization_router


def _org(org_id: str, *, personal: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=org_id, personal_owner_id="user-1" if personal else None, name=org_id, slug=org_id,
        avatar_url=None, plan="free", seat_limit=1, created_at=datetime(2026, 11, 9, tzinfo=UTC),
    )


class _Organizations:
    def list_my_orgs(self, user_id: str):
        assert user_id == "user-1"
        return [_org("me", personal=True), _org("acme", personal=False)]


def _list(plans):
    with patch.object(organization_router, "_published_plans", plans):
        response = organization_router.list_organizations(
            org_service=_Organizations(),  # type: ignore[arg-type]
            current_user=CurrentUser(user_id="user-1", email="a@example.com", role="authenticated"),
        )
    return {out.id: (out.kind, out.billing_plan_id) for out in response.data}


def test_each_space_carries_the_plan_billing_published() -> None:
    assert _list(lambda ids: {"me": "personal", "acme": "team"}) == {
        "me": ("space", "personal"),
        "acme": ("space", "team"),
    }


def test_a_space_without_a_published_plan_is_unlabelled_not_missing() -> None:
    assert _list(lambda ids: {}) == {"me": ("space", None), "acme": ("space", None)}
