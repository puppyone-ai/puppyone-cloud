"""Desktop's first push, exactly as Desktop sends it, for every commit graph
the Cloud Git acceptance contract accepts.

Desktop proves it sends these shapes against a bare remote
(puppyone-desktop tests/integration/cloud/publish/
electron.cloud-publish-repository-shapes.test.mjs). This proves a running Cloud
accepts them through the same requests: Desktop's create payload and one
idempotency key for project and credential, stock Git with Desktop's push
arguments and attempt header, then the operation receipt Desktop reads to
confirm. Both repositories pin contracts/cloud-git-acceptance-v1.json, so a
change to what Cloud accepts fails one side until the other follows
(puppy-issues ISSUE-127). Merge tips were refused by Desktop for a month after
Cloud began accepting them because nothing connected these two facts.
"""

import base64
import json
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.repository_hosting.harness.git import Git
from tests.repository_hosting.native.support import (
    SHAPES,
    authorization,
    parents,
    publish_entitlement,
    scoped,
)

pytestmark = [pytest.mark.hosting_application, pytest.mark.hosting_s3]

CONTRACT = Path(__file__).resolve().parents[4] / "contracts" / "cloud-git-acceptance-v1.json"


def desktop_create(app):
    """POST /projects/ then /git-credentials as Desktop does: one operation key."""
    org = app.api(
        "POST", "/organizations/", expected=201, json={"name": "Desktop push " + uuid.uuid4().hex}
    )["id"]
    publish_entitlement(app, org)
    operation = str(uuid.uuid4())
    project = app.api(
        "POST",
        "/projects/",
        expected=201,
        headers={"Idempotency-Key": operation},
        json={"org_id": org, "name": "Desktop first push", "description": None},
    )
    assert project["org_id"] == org
    secret = "pwg_" + secrets.token_urlsafe(32)
    issued = app.api(
        "POST",
        f"/projects/{project['id']}/git-credentials",
        expected=201,
        headers={"Idempotency-Key": operation},
        json={
            "target": {"kind": "project_root", "project_id": project["id"]},
            "mode": "rw",
            "credential": secret,
        },
    )
    remote = issued["remote"]["url"]
    # Desktop's canonical-remote check: same origin as the API, /git/<id>.git.
    assert urlsplit(remote).path == f"/git/{project['id']}.git"
    assert issued["remote"]["target"] == {"kind": "project_root", "project_id": project["id"]}
    return project["id"], remote, secret


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_cloud_accepts_desktop_first_push_of_every_contract_shape(
    bare_application, tmp_path, shape
):
    contract = json.loads(CONTRACT.read_text())
    assert contract["commit_graph"]["merge_commits"] == "accepted"
    build, tip_parents = SHAPES[shape]
    app = bare_application
    project, remote, secret = desktop_create(app)
    client = Git.init(tmp_path / "local")
    build(client)
    head = client.text("rev-parse", "HEAD")
    assert len(parents(client, "HEAD")) == tip_parents

    attempt = str(uuid.uuid4())
    pushed = client.run(
        *scoped(
            remote,
            f"Authorization: {authorization(secret)}",
            f"X-PuppyOne-Initial-Attempt: {attempt}",
        ),
        "push",
        "--progress",
        "--force-with-lease=refs/heads/main:",
        remote,
        f"{head}:refs/heads/main",
        check=False,
    )
    assert pushed.returncode == 0, pushed.stderr.decode()

    # The receipt Desktop reads to confirm an attempt it could not observe.
    receipt = app.request(
        "GET",
        urlsplit(remote).path + f"/operations/{attempt}",
        token=False,
        headers={"Authorization": authorization(secret)},
    ).json()["data"]
    assert receipt["project_id"] == project and receipt["request_key"] == attempt
    assert receipt["status"] == "committed" == receipt["result"]["status"]
    [ref] = receipt["result"]["refs"]
    assert ref["name_b64"] == base64.b64encode(b"refs/heads/main").decode()
    assert ref["observed"] == {"kind": "absent"} and ref["new"] == {"kind": "oid", "oid": head}

    cold = Git.init(tmp_path / "cold.git", bare=True)
    cold.run(
        *scoped(remote, f"Authorization: {authorization(secret)}"),
        "fetch",
        remote,
        "+refs/*:refs/*",
    )
    cold.run("fsck", "--full", "--strict")
    assert cold.text("rev-parse", "main") == head
    assert parents(cold, "main") == parents(client, "HEAD")
    assert cold.text("rev-list", "--count", "main") == client.text("rev-list", "--count", "HEAD")
