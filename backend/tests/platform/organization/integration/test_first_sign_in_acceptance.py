"""First sign-in and cross-module reads over real SQL, storage and JWTs."""

from uuid import uuid4

import pytest

from src.config import settings
from src.platform.auth import dependencies as auth_dependencies
from src.platform.billing import provisioning
from src.platform.onboarding import dependencies as onboarding

pytestmark = pytest.mark.integration


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"]


def initialize(t, person="newcomer"):
    return checked(t.request(person, "POST", "/auth/initialize"))


def projects_in(postgres, org):
    return postgres.row(f"SELECT count(*) AS total FROM projects WHERE org_id='{org}'")["total"]


@pytest.fixture(autouse=True)
def rebuilt_services(monkeypatch):
    monkeypatch.setattr(auth_dependencies, "_initialization_service", None)
    monkeypatch.setattr(onboarding, "_service", None)


class PaidProviderStandIn:
    """Only PuppyPay's answer is substituted; the entitlement it leaves is real.

    Storage refuses to publish for an Organization without one, so the demo
    Project below can only exist if this step really ran before it.
    """

    def __init__(self, postgres):
        self._postgres = postgres

    async def ensure(self, *, org_id, actor_user_id):
        self._postgres.sql(f"""
            INSERT INTO organization_usage_counters(org_id,metric,value,version)
            VALUES('{org_id}','storage.logical_bytes',0,1)
            ON CONFLICT(org_id,metric) DO NOTHING;
            INSERT INTO organization_entitlements(
                org_id,source,source_revision,payload_hash,entitlements
            ) VALUES(
                '{org_id}','puppypay',1,'{"a" * 64}',
                '{{"limits":{{"storage.max_bytes":1073741824,
                              "upload.max_single_file_bytes":10485760}}}}'
            ) ON CONFLICT(org_id) DO NOTHING;
        """)


@pytest.fixture
def newcomer(team, postgres, monkeypatch):
    """A signed-in identity the product has never seen."""
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db")
    monkeypatch.setattr(
        provisioning,
        "get_entitlement_provisioning_service",
        lambda: PaidProviderStandIn(postgres),
    )
    user = str(uuid4())
    team.users["newcomer"] = user
    team.emails["newcomer"] = f"newcomer-{user}@example.test"
    postgres.sql(f"INSERT INTO auth.users(id,email) VALUES('{user}','{team.emails['newcomer']}')")
    return user


def test_a_new_user_lands_in_a_ready_seeded_demo_project(team, newcomer, postgres):
    result = initialize(team)

    org, project = result["org_id"], result["demo_project_id"]
    assert result["is_new_org"] is True and project
    assert (
        postgres.row(
            f"SELECT role FROM org_members WHERE org_id='{org}' AND user_id='{newcomer}'"
        )["role"]
        == "owner"
    )
    assert postgres.row(
        f"SELECT name, org_id, created_by, lifecycle_status FROM projects WHERE id='{project}'"
    ) == {
        "name": "Get Started",
        "org_id": org,
        "created_by": newcomer,
        "lifecycle_status": "ready",
    }
    assert postgres.row(
        "SELECT has_onboarded, demo_project_id, default_org_id "
        f"FROM profiles WHERE user_id='{newcomer}'"
    ) == {"has_onboarded": True, "demo_project_id": project, "default_org_id": org}

    # The ordinary read paths prove the Project is usable, not merely recorded.
    assert [
        (row["id"], row["access_point_count"])
        for row in checked(
            team.request("newcomer", "GET", "/projects/", params={"org_id": org})
        )
    ] == [(project, 0)]
    readme = team.request(
        "newcomer", "GET", f"/content/{project}/resource", params={"path": "README.md"}
    )
    assert readme.status_code == 200, readme.text


def test_signing_in_again_changes_nothing(team, newcomer, postgres):
    first = initialize(team)

    again = initialize(team)

    assert again == {"org_id": first["org_id"], "is_new_org": False, "demo_project_id": None}
    assert projects_in(postgres, first["org_id"]) == 1


def test_a_user_who_already_has_projects_gets_no_demo_project(team, postgres):
    """The owner predates onboarding: their Project exists, the flag does not."""
    postgres.sql(f"UPDATE profiles SET has_onboarded=false WHERE user_id='{team.users['owner']}'")
    before = projects_in(postgres, team.org)

    result = initialize(team, "owner")

    assert result["demo_project_id"] is None
    assert projects_in(postgres, team.org) == before
    assert postgres.row(
        f"SELECT has_onboarded FROM profiles WHERE user_id='{team.users['owner']}'"
    ) == {"has_onboarded": True}


def _surface(team, postgres, kind):
    surface = str(uuid4())
    postgres.sql(f"""
        INSERT INTO access_surfaces(id,project_id,org_id,kind,name,created_by)
        VALUES('{surface}','{team.project}','{team.org}','{kind}','{kind} surface','{team.user}')
    """)
    return surface


def _binding(team, postgres):
    binding = str(uuid4())
    postgres.sql(f"""
        INSERT INTO synchronize_bindings(
            id,project_id,org_id,name,provider,target_path,direction,created_by
        ) VALUES(
            '{binding}','{team.project}','{team.org}','Imported','url','imported','inbound',
            '{team.user}'
        )
    """)
    return binding


def _count(team):
    return checked(team.request("owner", "GET", f"/projects/{team.project}"))["access_point_count"]


def test_project_entry_points_count_what_users_created_across_owners(team, postgres):
    before = _count(team)

    _surface(team, postgres, "mcp")
    assert _count(team) == before + 1

    _binding(team, postgres)
    assert _count(team) == before + 2

    # An Agent is a built-in way in, not an entry point the user created.
    _surface(team, postgres, "agent")
    assert _count(team) == before + 2


def test_the_dashboard_keeps_each_domain_on_real_data(team, postgres):
    surface = _surface(team, postgres, "mcp")
    binding = _binding(team, postgres)

    for person in ("owner", "viewer"):
        dashboard = checked(
            team.request(person, "GET", f"/projects/{team.project}/dashboard/resources")
        )
        kinds = {(row["resource_kind"], row["resource_id"]) for row in dashboard["resources"]}
        assert {("access", surface), ("synchronize", binding)} <= kinds
        assert dashboard["project"]["id"] == team.project
        assert dashboard["nodes"]["files"] >= 1


def test_the_dashboard_refuses_a_stranger(team):
    response = team.request("outsider", "GET", f"/projects/{team.project}/dashboard/resources")
    assert response.status_code in (403, 404), response.text
