"""No hosted credentials, external emails, paid inference or customer scans."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from src.platform.authorization.models import ProjectAction
from src.platform.authorization.repository import AuthorizationRepository
from src.platform.authorization.service import AuthorizationService
from tests.agent.runtime.test_data_access import measured
from tests.platform.organization.permission_matrix import ACTION_MINIMUM_ROLE, expected_allowed

pytestmark = pytest.mark.integration


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json().get("data", response.json())


def invite(t, person="invitee", role="member"):
    return checked(
        t.request(
            "owner",
            "POST",
            f"/organizations/{t.org}/invite",
            json={"email": t.emails[person], "role": role},
        )
    )


def resource(t, person, path="文档/hello #1.md", status=200):
    return checked(
        t.request(person, "GET", f"/content/{t.project}/resource", params={"path": path}), status
    )


def write(t, person, path="draft.md", status=200):
    return checked(
        t.request(
            person,
            "POST",
            f"/content/{t.project}/write",
            json={"path": path, "content": "a team edit", "node_type": "markdown"},
        ),
        status,
    )


def set_role(t, person, role, actor="admin"):
    return checked(
        t.request(
            actor,
            "PUT",
            f"/projects/{t.project}/members/{t.users[person]}/role",
            json={"role": role},
        )
    )


def test_every_action_uses_current_database_facts_with_one_query(team, control_api):
    t = team
    repo = AuthorizationRepository(control_api)
    for person, role in {
        "owner": "admin",
        "admin": "admin",
        "editor": "editor",
        "viewer": "viewer",
        "member": None,
        "outsider": None,
    }.items():
        grant, counts = measured(
            "acceptance.permission-facts",
            lambda person=person: AuthorizationService(repo).resolve_project_grant(
                t.project, t.users[person]
            ),
        )
        assert counts["attempts"] == 1, counts
        for action in ACTION_MINIMUM_ROLE:
            assert bool(grant and grant.allows(ProjectAction(action))) == expected_allowed(
                role, action
            ), (person, action)


@pytest.mark.parametrize(
    "person,read_status,write_status,admin_status",
    [
        ("owner", 200, 200, 200),
        ("admin", 200, 200, 200),
        ("editor", 200, 200, 403),
        ("viewer", 200, 403, 403),
        ("member", 404, 404, 404),
        ("outsider", 404, 404, 404),
        (None, 401, 401, 401),
    ],
)
def test_real_http_role_matrix(team, person, read_status, write_status, admin_status):
    t = team
    resource(t, person, status=read_status)
    resource(t, person, path="文档", status=read_status)
    write(t, person, status=write_status)
    checked(t.request(person, "GET", f"/projects/{t.project}/members"), admin_status)
    checked(
        t.request(person, "PUT", f"/projects/{t.project}", json={"name": "Reviewed workspace"}),
        admin_status,
    )
    checked(
        t.request(
            person,
            "POST",
            "/agents/runs",
            json={
                "project_id": t.project,
                "agent_id": t.agent,
                "request_id": str(uuid4()),
                "prompt": "Read these files",
            },
        ),
        202 if write_status == 200 else write_status,
    )
    # Every identity, including this org's Owner, is denied in another tenant.
    if person != "outsider":
        checked(
            t.request(person, "GET", f"/projects/{t.other_project}/authorization"),
            401 if person is None else 404,
        )


def test_invitation_workspace_grant_downgrade_and_revocation_with_same_login(team):
    t = team
    invitation = invite(t)
    assert t.outbox == [
        {"email": t.emails["invitee"], "url": invitation["invite_url"], "org": "Agent fixture"}
    ]
    checked(
        t.request("invitee", "POST", f"/organizations/invitations/{invitation['token']}/accept")
    )
    resource(t, "invitee", status=404)  # org admission does not grant private workspace access
    checked(
        t.request(
            "admin",
            "POST",
            f"/projects/{t.project}/members",
            json={"user_id": t.users["invitee"], "role": "editor"},
        ),
        201,
    )
    same_token = t.token("invitee")
    read_url = f"/api/v1/content/{t.project}/resource?path=draft.md"
    write(t, "invitee")
    assert (
        checked(t.http.get(read_url, headers={"Authorization": "Bearer " + same_token}))[
            "content_text"
        ]
        == "a team edit"
    )
    set_role(t, "invitee", "viewer")
    resource(t, "invitee")
    write(t, "invitee", status=403)
    checked(
        t.request(
            "invitee",
            "PUT",
            f"/projects/{t.project}/members/{t.users['invitee']}/role",
            json={"role": "admin"},
        ),
        403,
    )
    checked(t.request("admin", "DELETE", f"/projects/{t.project}/members/{t.users['invitee']}"))
    checked(t.http.get(read_url, headers={"Authorization": "Bearer " + same_token}), 404)
    # Removed members must not resurrect membership with a previously used link.
    checked(t.request("owner", "DELETE", f"/organizations/{t.org}/members/{t.users['invitee']}"))
    checked(
        t.request("invitee", "POST", f"/organizations/invitations/{invitation['token']}/accept"),
        404,
    )


def test_org_visibility_is_read_only_and_private_visibility_takes_effect(team):
    t = team
    checked(t.request("admin", "PUT", f"/projects/{t.project}", json={"visibility": "org"}))
    resource(t, "member")
    write(t, "member", status=403)
    resource(t, "outsider", status=404)
    checked(t.request("admin", "PUT", f"/projects/{t.project}", json={"visibility": "private"}))
    resource(t, "member", status=404)
    resource(t, "editor")


def test_workspace_admin_cannot_manage_organization_or_add_outsider(team):
    t = team
    for person in ("admin", "editor", "viewer", "member", "outsider"):
        for method, route, payload in (
            ("PUT", f"/organizations/{t.org}", {"name": "Escalated"}),
            ("POST", f"/organizations/{t.org}/invite", {"email": t.emails["invitee"]}),
            ("PUT", f"/organizations/{t.org}/members/{t.users['viewer']}/role", {"role": "member"}),
            ("DELETE", f"/organizations/{t.org}/members/{t.users['viewer']}", None),
            ("POST", f"/organizations/{t.org}/ownership/transfer", {"user_id": t.users["viewer"]}),
            ("DELETE", f"/organizations/{t.org}", None),
        ):
            checked(t.request(person, method, route, **({"json": payload} if payload else {})), 403)
    checked(
        t.request(
            "admin",
            "POST",
            f"/projects/{t.project}/members",
            json={"user_id": t.users["outsider"], "role": "admin"},
        ),
        403,
    )
    assert not t.outbox


def test_owner_transfer_leave_and_organization_revocation(team):
    t = team
    checked(
        t.request(
            "owner",
            "POST",
            f"/organizations/{t.org}/ownership/transfer",
            json={"user_id": t.users["member"]},
        )
    )
    # Workspace authority is recomputed from the new organization Owner.
    checked(t.request("member", "GET", f"/projects/{t.project}/members"))
    checked(
        t.request("member", "DELETE", f"/organizations/{t.org}/members/{t.users['owner']}"), 409
    )
    checked(
        t.request(
            "owner", "POST", f"/organizations/{t.org}/invite", json={"email": t.emails["invitee"]}
        ),
        403,
    )
    checked(t.request("member", "DELETE", f"/organizations/{t.org}/members/{t.users['editor']}"))
    resource(t, "editor", status=404)
    write(t, "editor", status=404)
    checked(t.request("viewer", "POST", f"/organizations/{t.org}/leave"))
    resource(t, "viewer", status=404)


def test_invite_wrong_email_expired_revoked_and_existing_member_never_upgrades(team, postgres):
    t = team
    invitation = invite(t)
    wrong = t.request(
        "outsider", "POST", f"/organizations/invitations/{invitation['token']}/accept"
    )
    assert wrong.status_code == 403 and "different email" in wrong.text
    checked(t.request("owner", "DELETE", f"/organizations/{t.org}/invitations/{invitation['id']}"))
    checked(
        t.request("invitee", "POST", f"/organizations/invitations/{invitation['token']}/accept"),
        404,
    )
    expired = invite(t)
    postgres.sql(
        f"UPDATE org_invitations SET expires_at=now()-interval '1 second' WHERE id='{expired['id']}'"
    )
    response = t.request("invitee", "POST", f"/organizations/invitations/{expired['token']}/accept")
    assert response.status_code == 403 and "expired" in response.text
    checked(
        t.request(
            "owner",
            "PUT",
            f"/organizations/{t.org}/members/{t.users['viewer']}/role",
            json={"role": "viewer"},
        )
    )
    existing = invite(t, "viewer", "member")
    checked(t.request("viewer", "POST", f"/organizations/invitations/{existing['token']}/accept"))
    assert (
        postgres.row(
            f"SELECT role FROM org_members WHERE org_id='{t.org}' AND user_id='{t.users['viewer']}'"
        )["role"]
        == "viewer"
    )


def test_atomic_invitation_rechecks_revocation_after_application_preflight(
    team, monkeypatch, postgres
):
    t = team
    invitation = invite(t)
    repo = t.org_service._repo
    redeem = repo.redeem_invitation

    def revoked_during_admission(*args, **kwargs):
        repo.revoke_invitation(t.org, invitation["id"])
        return redeem(*args, **kwargs)

    monkeypatch.setattr(repo, "redeem_invitation", revoked_during_admission)
    checked(
        t.request("invitee", "POST", f"/organizations/invitations/{invitation['token']}/accept"),
        404,
    )
    assert (
        postgres.row(
            f"SELECT * FROM org_members WHERE org_id='{t.org}' AND user_id='{t.users['invitee']}'"
        )
        is None
    )
    assert (
        postgres.row(f"SELECT status FROM org_invitations WHERE id='{invitation['id']}'")["status"]
        == "revoked"
    )


def test_concurrent_invite_redemption_has_one_membership_and_one_admission(
    team, control_api, postgres
):
    t = team
    invitation = invite(t)

    def redeem(_):
        return (
            control_api.rpc(
                "accept_organization_invitation",
                {
                    "p_token": invitation["token"],
                    "p_user_id": t.users["invitee"],
                    "p_user_email": t.emails["invitee"],
                    "p_enforce_local_seat_limit": True,
                },
            )
            .execute()
            .data["outcome"]
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(redeem, range(8)))
    assert outcomes.count("accepted") == 1 and outcomes.count("unavailable") == 7
    assert (
        postgres.row(
            f"SELECT count(*) AS count FROM org_members WHERE org_id='{t.org}' AND user_id='{t.users['invitee']}'"
        )["count"]
        == 1
    )


def test_invitation_rpc_is_backend_only_and_populated_upgrade_preserves_rows(team, postgres):
    t = team
    invitation = invite(t)
    before = postgres.row(f"SELECT * FROM org_invitations WHERE id='{invitation['id']}'")
    postgres.sql("DROP FUNCTION accept_organization_invitation(text,uuid,text,boolean)")
    migration = (
        Path(__file__).resolve().parents[5]
        / "supabase/migrations/20261010010000_atomic_organization_invitation_acceptance.sql"
    )
    postgres.sql(migration.read_text())
    assert postgres.row(f"SELECT * FROM org_invitations WHERE id='{invitation['id']}'") == before
    for role in ("anon", "authenticated"):
        with pytest.raises(RuntimeError, match="permission denied"):
            postgres.sql(
                f"SET ROLE {role}; SELECT accept_organization_invitation('x','{t.users['invitee']}','x',true)"
            )
    checked(
        t.request("invitee", "POST", f"/organizations/invitations/{invitation['token']}/accept")
    )


def test_creator_guard_duplicate_member_invalid_role_and_stale_admin(team):
    t = team
    checked(t.request("admin", "DELETE", f"/projects/{t.project}/members/{t.users['owner']}"), 409)
    checked(
        t.request(
            "admin",
            "PUT",
            f"/projects/{t.project}/members/{t.users['owner']}/role",
            json={"role": "viewer"},
        ),
        409,
    )
    checked(
        t.request(
            "admin",
            "POST",
            f"/projects/{t.project}/members",
            json={"user_id": t.users["editor"], "role": "editor"},
        ),
        409,
    )
    checked(
        t.request(
            "owner",
            "PUT",
            f"/organizations/{t.org}/members/{t.users['viewer']}/role",
            json={"role": "administrator"},
        ),
        422,
    )
    checked(
        t.request("owner", "POST", f"/organizations/{t.org}/invite", json={"email": "   "}), 422
    )
    set_role(t, "admin", "viewer", actor="owner")
    checked(
        t.request(
            "admin",
            "POST",
            f"/projects/{t.project}/members",
            json={"user_id": t.users["member"], "role": "admin"},
        ),
        403,
    )


def test_concurrent_distinct_invitations_cannot_overbook_final_local_seat(
    team, control_api, postgres
):
    t = team
    first, second = invite(t), invite(t, "outsider")
    postgres.sql(
        f"UPDATE organizations SET seat_limit=(SELECT count_billable_organization_members('{t.org}'))+1 WHERE id='{t.org}'"
    )

    def redeem(pair):
        person, invitation = pair
        return (
            control_api.rpc(
                "accept_organization_invitation",
                {
                    "p_token": invitation["token"],
                    "p_user_id": t.users[person],
                    "p_user_email": t.emails[person],
                    "p_enforce_local_seat_limit": True,
                },
            )
            .execute()
            .data["outcome"]
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(redeem, [("invitee", first), ("outsider", second)]))
    assert sorted(outcomes) == ["accepted", "seat_limit"]


def test_old_owner_invitation_loses_authority_after_transfer(team, control_api):
    t = team
    invitation = invite(t)
    checked(
        t.request(
            "owner",
            "POST",
            f"/organizations/{t.org}/ownership/transfer",
            json={"user_id": t.users["member"]},
        )
    )
    checked(
        t.request("invitee", "POST", f"/organizations/invitations/{invitation['token']}/accept"),
        404,
    )


def test_cross_org_invite_revocation_and_missing_or_expired_identity(team):
    t = team
    invitation = invite(t)
    checked(
        t.request(
            "outsider", "DELETE", f"/organizations/{t.other_org}/invitations/{invitation['id']}"
        )
    )
    checked(
        t.request("invitee", "POST", f"/organizations/invitations/{invitation['token']}/accept")
    )
    for token in (
        "not-a-jwt",
        t.token("owner", exp=1),
        t.token("owner", iss="https://untrusted.test/auth/v1"),
    ):
        checked(
            t.http.get(
                f"/api/v1/content/{t.project}/resource",
                headers={"Authorization": "Bearer " + token},
            ),
            401,
        )


def test_owner_operations_and_self_protection(team):
    t = team
    checked(t.request("owner", "PUT", f"/organizations/{t.org}", json={"name": "Writing team"}))
    checked(t.request("owner", "GET", f"/organizations/{t.org}/members"))
    checked(t.request("owner", "GET", f"/organizations/{t.org}/invitations"))
    for method, suffix, body in (
        ("PUT", f"/members/{t.users['owner']}/role", {"role": "member"}),
        ("DELETE", f"/members/{t.users['owner']}", None),
        ("POST", "/leave", None),
        ("PUT", f"/members/{t.users['viewer']}/role", {"role": "owner"}),
        ("POST", "/invite", {"email": t.emails["invitee"], "role": "owner"}),
    ):
        checked(
            t.request(
                "owner",
                method,
                f"/organizations/{t.org}{suffix}",
                **({"json": body} if body else {}),
            ),
            403,
        )
    checked(t.request("owner", "DELETE", f"/organizations/{t.org}"), 403)
    spare = checked(
        t.request("owner", "POST", "/organizations/", json={"name": "Spare", "slug": str(uuid4())}),
        201,
    )
    # Deletion cannot cascade past workspace cleanup, even for an Owner.
    checked(t.request("owner", "DELETE", f"/organizations/{t.org}"), 409)
    checked(t.request("owner", "DELETE", f"/organizations/{spare['id']}"))


def test_editor_folder_move_delete_and_viewer_rejection(team):
    t = team
    operations = [
        ("mkdir", {"path": "drafts"}),
        ("write", {"path": "drafts/note.md", "content": "Working draft", "node_type": "markdown"}),
        ("mv", {"old_path": "drafts", "new_path": "review"}),
        ("rm", {"path": "review", "recursive": True}),
    ]
    for operation, body in operations:
        for person, status in [("viewer", 403), ("editor", 200)]:
            checked(
                t.request(person, "POST", f"/content/{t.project}/{operation}", json=body), status
            )
        if operation == "mv":
            assert resource(t, "viewer", path="review/note.md")["content_text"] == "Working draft"
            resource(t, "viewer", path="drafts/note.md", status=404)
    resource(t, "viewer", path="review", status=404)
