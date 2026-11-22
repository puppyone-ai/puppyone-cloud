"""Real PostgreSQL 17 fixtures for the billing SQL functions."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tests.platform.workspace.native.conftest import ROOT, NativeDatabase


@pytest.fixture(scope="session")
def pg():
    spec = importlib.util.spec_from_file_location(
        "billing_native_postgres", ROOT / "scripts/testing/native_postgres.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with module.native_postgres() as cluster:
        yield NativeDatabase(cluster["url"])


def space(pg: NativeDatabase, *, members: int = 1) -> dict:
    """A team Space with an owner and ``members - 1`` more billable members."""

    org = f"billing-{uuid4()}"
    people = [str(uuid4()) for _ in range(members)]
    statements = ["BEGIN;"]
    statements += [f"INSERT INTO auth.users(id) VALUES ('{person}');" for person in people]
    statements.append(
        f"INSERT INTO public.organizations(id,name,slug,type,created_by) "
        f"VALUES ('{org}','Billing native','{org}','team','{people[0]}');"
    )
    for index, person in enumerate(people):
        role = "owner" if index == 0 else "member"
        statements.append(
            f"INSERT INTO public.org_members(org_id,user_id,role) VALUES ('{org}','{person}','{role}');"
        )
    statements.append("COMMIT;")
    pg.sql("\n".join(statements))
    return {"org": org, "owner": people[0], "members": people}


def entitle(
    pg: NativeDatabase,
    org: str,
    *,
    seats: int,
    revision: int,
    plan: str = "team_v2",
    quote: str | None = None,
) -> None:
    """Publish the PuppyPay projection through the function PuppyPay's publication uses."""

    pg.call_rpc(
        "publish_organization_entitlement_v2",
        org_id=org,
        schema_version="1.1",
        plan_id=plan,
        status="active",
        source="puppypay",
        entitlements={"features": {}, "limits": {"seats.purchased": seats}, "allow": {}},
        seat_quantity=seats,
        catalog_version="2026-11-09.2",
        source_revision=revision,
        effective_at=datetime.now(UTC).isoformat(),
        effective_until=None,
        current_period_end=None,
        payload_hash=f"{revision:064x}",
        source_event_id=f"event-{revision}",
        source_quote_id=quote,
    )


def person(pg: NativeDatabase) -> str:
    user = str(uuid4())
    pg.sql(f"INSERT INTO auth.users(id) VALUES ('{user}');")
    return user
