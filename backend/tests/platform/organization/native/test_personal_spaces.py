import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest


def ensure(db, user=None):
    return json.loads(db.sql(f"SET ROLE service_role; SELECT public.ensure_personal_cloud_space('{user or db.owner}', 'Personal account')"))


def test_populated_upgrade_preserves_team_projects_members_and_default(database):
    db = database
    org = json.loads(db.sql(f"SELECT to_jsonb(o) FROM organizations o WHERE id='{db.team}'"))
    assert org.pop('personal_owner_id') is None
    assert org == db.before['org']
    assert json.loads(db.sql(f"SELECT to_jsonb(p) FROM projects p WHERE id='{db.project}'")) == db.before['project']
    assert db.sql(f"SELECT count(*) FROM org_members WHERE org_id='{db.team}'") == '2'
    default = ensure(db)['organization']
    assert default['id'] == db.team
    assert db.sql(f"SELECT default_org_id FROM profiles WHERE user_id='{db.owner}'") == db.team
    assert db.sql(f"SELECT org_id FROM projects WHERE id='{db.project}'") == db.team


def test_concurrent_creation_is_atomic_and_idempotent(database):
    db = database
    user = str(uuid4())
    db.sql(f"INSERT INTO auth.users(id) VALUES('{user}'); INSERT INTO profiles(user_id,email) VALUES('{user}','{user}@example.test') ON CONFLICT DO NOTHING;")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: ensure(db, user), range(8)))
    assert sum(row['created'] for row in results) == 1
    assert len({row['organization']['id'] for row in results}) == 1
    org = results[0]['organization']
    assert org['personal_owner_id'] is None and org['type'] == 'team'
    assert db.sql(f"SELECT default_org_id FROM profiles WHERE user_id='{user}'") == org['id']
    assert json.loads(db.sql(f"SELECT jsonb_agg(jsonb_build_array(user_id,role)) FROM org_members WHERE org_id='{org['id']}'")) == [[user, 'owner']]


@pytest.mark.parametrize('role', ['anon', 'authenticated'])
def test_provisioning_is_not_a_direct_client_rpc(database, role):
    with pytest.raises(RuntimeError, match='permission denied'):
        database.sql(f"SET ROLE {role}; SELECT public.ensure_personal_cloud_space('{database.owner}', 'Forged')")


def test_uninitialized_identity_cannot_create_a_space(database):
    with pytest.raises(RuntimeError, match='initialized account'):
        ensure(database, str(uuid4()))


def test_former_personal_space_can_grow_without_widening_old_projects(database):
    db = database
    org = db.personal
    assert db.sql(f"SELECT visibility FROM projects WHERE id='{db.personal_project}'") == 'private'
    assert db.sql(f"SELECT org_id FROM projects WHERE id='{db.personal_project}'") == org
    db.sql(f"INSERT INTO org_members(org_id,user_id,role) VALUES('{org}','{db.colleague}','viewer');"
           f"INSERT INTO org_invitations(org_id,email,role,token,invited_by,expires_at) VALUES('{org}','other@example.test','member','unified-test','{db.owner}',now()+interval '1 day');")
    assert db.sql(f"SELECT count(*) FROM org_members WHERE org_id='{org}'") == '2'
    assert db.sql(f"SELECT count(*) FROM project_members WHERE project_id='{db.personal_project}' AND user_id='{db.colleague}'") == '0'
    assert db.sql(f"SELECT transfer_organization_ownership('{org}','{db.owner}','{db.colleague}')") == 't'
    result = json.loads(db.sql(f"SELECT delete_empty_organization_control_plane('{org}','{db.colleague}')"))
    assert result['outcome'] == 'organization_not_empty'
    # Historical creator identity cannot cascade-delete a collaborating Space.
    assert db.sql("SELECT confdeltype FROM pg_constraint WHERE conname='organizations_personal_owner_id_fkey'") == 'n'


def test_ordinary_team_membership_still_works(database):
    db = database
    user = str(uuid4())
    db.sql(f"INSERT INTO auth.users(id) VALUES('{user}'); INSERT INTO profiles(user_id,email) VALUES('{user}','{user}@example.test') ON CONFLICT DO NOTHING;")
    db.sql(f"INSERT INTO org_members(org_id,user_id,role) VALUES('{db.team}','{user}','viewer');"
           f"UPDATE org_members SET role='member' WHERE org_id='{db.team}' AND user_id='{user}';"
           f"DELETE FROM org_members WHERE org_id='{db.team}' AND user_id='{user}';")
    assert db.sql(f"SELECT count(*) FROM org_members WHERE org_id='{db.team}'") == '2'
