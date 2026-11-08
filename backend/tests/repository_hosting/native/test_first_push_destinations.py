"""A folder's first push into the space Desktop chose, through the actual application.

Desktop puts a local folder in the Cloud with three calls from the publish contract
(contracts/cloud-project-publish-v1.json): create an empty Project in an explicit
Organization, issue a project_root credential, push. These run them against the
real API process, Supabase Auth, S3 and stock Git, into a personal space and a
team, and check where the Project is listed afterwards.
"""
import base64
import hashlib
import json
import os
import secrets
import uuid
from datetime import UTC, datetime

import pytest

from src.platform.entitlements.models import (
    REQUIRED_ENTITLEMENT_FEATURES,
    REQUIRED_ENTITLEMENT_LIMITS,
    EntitlementUpsert,
)
from tests.repository_hosting.harness.application import Application
from tests.repository_hosting.harness.git import Git
from tests.repository_hosting.harness.supabase_api import SupabaseAPI

pytestmark = pytest.mark.hosting_application


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    application = Application(tmp_path_factory.mktemp("first-push"),
                              profile="first push into a chosen personal or team space")
    try:
        application.start()
        yield application
    finally:
        application.close()


ENTITLED = set()


def entitle(app, org):
    """Billing publishes the space's plan at its authenticated ingress, as it does in production, once per space."""
    if org in ENTITLED:
        return
    ENTITLED.add(org)
    limits = {key: 100 for key in REQUIRED_ENTITLEMENT_LIMITS}
    limits.update({"storage.max_bytes": 64 * 1024**2, "upload.max_single_file_bytes": 8 * 1024**2, "seats.purchased": 1})
    payload = EntitlementUpsert(
        org_id=org, schema_version="1.0", plan_id="test-first-push", status="active", source="puppypay",
        entitlements={"features": {key: True for key in REQUIRED_ENTITLEMENT_FEATURES}, "limits": limits,
                      "allow": {"access_surface_kinds": "*"}},
        seat_quantity=1, catalog_version="test-1", source_revision=1, effective_at=datetime.now(UTC), payload_hash="0" * 64,
    ).model_dump(mode="json")
    unsigned = {key: value for key, value in payload.items() if key != "payload_hash" and not (key == "source_quote_id" and value is None)}
    payload["payload_hash"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    response = app.client.post("/internal/billing/entitlements/upsert", json=payload,
                               headers={"X-Internal-Secret": app.env["INTERNAL_API_SECRET"]})
    assert response.status_code == 200, response.text


def first_push(app, org, folder):
    """Desktop's first push: create once, credential, push, exactly as the publish contract orders them."""
    key = str(uuid.uuid4())
    request = {"name": folder.name, "org_id": org}
    project = app.api("POST", "/projects/", expected=201, headers={"Idempotency-Key": key}, json=request)
    # A retried creation after a lost response is the same Project, never a second one.
    assert app.api("POST", "/projects/", expected=200, headers={"Idempotency-Key": key}, json=request)["id"] == project["id"]
    assert project["org_id"] == org
    secret = "pwg_" + secrets.token_urlsafe(32)
    remote = app.api("POST", f"/projects/{project['id']}/git-credentials", expected=201,
                     headers={"Idempotency-Key": str(uuid.uuid4())},
                     json={"target": {"kind": "project_root", "project_id": project["id"]}, "mode": "rw", "credential": secret},
                     )["remote"]["url"]
    client = Git.init(folder)
    header = "Authorization: Basic " + base64.b64encode(("x-puppyone-token:" + secret).encode()).decode()
    client.run("config", "--local", "http.extraHeader", header)
    (folder / ".git/config").chmod(0o600)
    client.run("branch", "-m", "main")
    commit = client.commit({"README.md": f"# {folder.name}\n".encode()})
    client.run("remote", "add", "origin", remote)
    client.run("push", "-u", "origin", "main")
    assert app.api("GET", f"/projects/{project['id']}/readiness")["git"]["state"] == "ready"
    return project["id"], commit


def listed(app, org):
    return {row["id"] for row in app.api("GET", f"/projects/?org_id={org}")}


def test_a_first_push_into_my_personal_space_is_listed_there_only(app, tmp_path):
    personal = app.api("POST", "/auth/personal-space")
    assert personal["kind"] == "personal" and personal["can_create_projects"]
    entitle(app, personal["id"])
    team = app.api("POST", "/organizations/", expected=201, json={"name": "Design " + uuid.uuid4().hex})["id"]
    project, commit = first_push(app, personal["id"], tmp_path / "Journal")
    assert project in listed(app, personal["id"])
    assert project not in listed(app, team)
    assert app.api("GET", f"/content/{project}/head")["head_commit_id"] == commit


def test_a_first_push_into_a_team_is_listed_in_that_team(app, tmp_path):
    team = app.api("POST", "/organizations/", expected=201, json={"name": "Research " + uuid.uuid4().hex})["id"]
    entitle(app, team)
    project, commit = first_push(app, team, tmp_path / "Plans")
    assert project in listed(app, team)
    personal = app.api("POST", "/auth/personal-space")["id"]
    assert project not in listed(app, personal)
    assert app.api("GET", f"/content/{project}/head")["head_commit_id"] == commit


def test_nobody_else_can_start_a_project_in_my_personal_space(app):
    personal = app.api("POST", "/auth/personal-space")["id"]
    entitle(app, personal)
    before = listed(app, personal)
    other = SupabaseAPI(os.environ)
    try:
        other.authenticate()
        response = app.client.post("/api/v1/projects/", json={"name": "Intruder", "org_id": personal}, headers={
            "Idempotency-Key": str(uuid.uuid4()), "X-PuppyOne-Repository-Contract": "2",
            "Authorization": other.headers("authenticated")["Authorization"]})
        assert response.status_code in (403, 404), response.text
        # Their own spaces never include mine.
        theirs = app.client.get("/api/v1/organizations/", headers={
            "Authorization": other.headers("authenticated")["Authorization"]}).json()["data"]
        assert personal not in {row["id"] for row in theirs}
    finally:
        other.close()
    assert listed(app, personal) == before
