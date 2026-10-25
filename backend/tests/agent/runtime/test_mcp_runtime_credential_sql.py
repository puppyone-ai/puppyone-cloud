"""Real PostgreSQL admission of Project-root MCP keys (owner's current role, narrowed mode)."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def mcp(postgres):
    """An Organization with an owner and one member who owns a Project-root MCP key."""
    owner, member, org, project, surface, credential = [str(uuid4()) for _ in range(6)]
    postgres.sql(f"""
      BEGIN;
      INSERT INTO auth.users(id) VALUES ('{owner}'),('{member}');
      INSERT INTO public.organizations(id,name,slug,created_by)
        VALUES ('{org}','MCP fixture','mcp-{org}','{owner}');
      INSERT INTO public.org_members(org_id,user_id,role) VALUES ('{org}','{owner}','owner'),('{org}','{member}','member');
      INSERT INTO public.projects(id,name,org_id,created_by,lifecycle_status,visibility)
        VALUES ('{project}','MCP fixture','{org}','{owner}','ready','private');
      INSERT INTO public.project_members(project_id,org_id,user_id,role)
        VALUES ('{project}','{org}','{owner}','admin'),('{project}','{org}','{member}','editor');
      INSERT INTO public.access_surfaces(id,project_id,org_id,kind,name,created_by)
        VALUES ('{surface}','{project}','{org}','mcp','MCP fixture','{member}');
      INSERT INTO public.access_surface_policies(access_surface_id,fs_policy)
        VALUES ('{surface}','{{"accesses":[{{"path":"","readonly":false}}]}}'::jsonb);
      INSERT INTO public.access_surface_credentials(id,org_id,project_id,access_surface_id,credential_type,
        key_prefix,key_last4,key_hash,grant_mode,credential_lifecycle)
        VALUES ('{credential}','{org}','{project}','{surface}','bearer_token','mcp','last','hash-{credential}','rw','shared');
      COMMIT;
    """)
    return dict(
        owner=owner, member=member, org=org, project=project, surface=surface, credential=credential
    )


def mode(db, f):
    row = db.row(
        f"SELECT effective_mode, owner_id::text FROM public.resolve_mcp_runtime_credential('{f['credential']}')"
    )
    return row and row["effective_mode"]


def admit(db, f, *, write):
    db.sql(
        "SELECT public._version_assert_current_repository_actor("
        f"'{f['project']}','{f['org']}','runtime:{f['credential']}',{str(write).lower()});"
    )


def test_an_editor_owned_key_reads_and_writes(postgres, mcp):
    assert mode(postgres, mcp) == "rw"
    admit(postgres, mcp, write=False)
    admit(postgres, mcp, write=True)


def test_a_viewer_owner_caps_the_key_to_read_only_at_publication(postgres, mcp):
    postgres.sql(
        f"UPDATE project_members SET role='viewer' WHERE project_id='{mcp['project']}' AND user_id='{mcp['member']}'"
    )
    assert mode(postgres, mcp) == "r"
    admit(postgres, mcp, write=False)
    with pytest.raises(RuntimeError, match="repository_action_denied"):
        admit(postgres, mcp, write=True)


def test_a_read_only_policy_narrows_an_editor(postgres, mcp):
    postgres.sql(
        'UPDATE access_surface_policies SET fs_policy=\'{"accesses":[{"path":"","readonly":true}]}\''
        f" WHERE access_surface_id='{mcp['surface']}'"
    )
    assert mode(postgres, mcp) == "r"
    with pytest.raises(RuntimeError, match="repository_action_denied"):
        admit(postgres, mcp, write=True)


def test_removing_the_owner_denies_the_key_immediately(postgres, mcp):
    postgres.sql(
        f"DELETE FROM project_members WHERE project_id='{mcp['project']}' AND user_id='{mcp['member']}'"
    )
    assert mode(postgres, mcp) is None
    with pytest.raises(RuntimeError, match="repository_action_denied"):
        admit(postgres, mcp, write=False)


def test_org_visibility_gives_the_owner_only_a_viewer_baseline(postgres, mcp):
    postgres.sql(f"""
      DELETE FROM project_members WHERE project_id='{mcp["project"]}' AND user_id='{mcp["member"]}';
      UPDATE projects SET visibility='org' WHERE id='{mcp["project"]}';
    """)
    assert mode(postgres, mcp) == "r"


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE access_surface_credentials SET status='revoked' WHERE id='{credential}'",
        "UPDATE access_surface_credentials SET expires_at=now()-interval '1 minute' WHERE id='{credential}'",
        "UPDATE access_surfaces SET status='paused' WHERE id='{surface}'",
    ],
)
def test_revoked_expired_or_paused_keys_are_never_admitted(postgres, mcp, change):
    postgres.sql(change.format(**mcp))
    assert mode(postgres, mcp) is None
    with pytest.raises(RuntimeError, match="repository_action_denied"):
        admit(postgres, mcp, write=False)


def test_scope_bound_keys_are_not_admitted(postgres, mcp):
    scope = str(uuid4())
    postgres.sql(f"""
      INSERT INTO public.repository_scopes(id,project_id,name,path,exclude,max_mode)
        VALUES ('{scope}','{mcp["project"]}','docs','docs','[]'::jsonb,'rw');
      UPDATE access_surfaces SET scope_id='{scope}' WHERE id='{mcp["surface"]}';
    """)
    assert mode(postgres, mcp) is None


def test_clients_cannot_call_the_resolver(postgres, mcp):
    for role in ("anon", "authenticated"):
        assert postgres.row(
            "SELECT has_function_privilege("
            f"'{role}','public.resolve_mcp_runtime_credential(text)','EXECUTE') AS allowed"
        ) == {"allowed": False}
