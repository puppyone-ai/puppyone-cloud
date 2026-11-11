"""Desktop's own first-push code, run against a running Cloud.

test_desktop_first_push.py replays Desktop's requests in Python. This
runs the code itself: the owned Docker runner mounts a Desktop checkout
read-only (run_repository_hosting.py --docker --live --s3 --desktop <path>)
and Node executes its coordinator, journal, Cloud publish API, Git service and
receipt reader through tests/support/cloud/firstPushLiveDriver.mjs. Only the
OS keychain is stood in for. Without --desktop the test is deselected, so it
is neither passed nor counted as a skip (puppy-issues ISSUE-127).
"""

import json
import os
import secrets
import shutil
import subprocess
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

pytestmark = [pytest.mark.hosting_application, pytest.mark.hosting_s3, pytest.mark.hosting_desktop]

DESKTOP = os.environ.get("HOSTING_TEST_DESKTOP")
DRIVER = "tests/support/cloud/firstPushLiveDriver.mjs"


@pytest.fixture
def desktop():
    if not DESKTOP or not (Path(DESKTOP) / DRIVER).is_file():
        pytest.fail("the mounted Desktop checkout has no first-push live driver")
    node = shutil.which("node")
    if not node:
        pytest.fail("the Docker runner image has no Node; rebuild it from Dockerfile.hosting")
    return node, Path(DESKTOP)


def current_user(app):
    response = app.auth.request("GET", "/auth/v1/user", role="authenticated")
    assert response.status_code == 200
    return response.json()["id"]


def run_desktop(node, checkout, request):
    completed = subprocess.run(
        [node, str(checkout / DRIVER)],
        input=json.dumps(request).encode(),
        capture_output=True,
        timeout=600,
        check=False,
        # Node, like Git, must not inherit a proxy or a host configuration.
        env={
            "PATH": os.environ["PATH"],
            "HOME": os.environ.get("HOME", "/tmp/home"),
            "NO_PROXY": "*",
        },
    )
    assert completed.returncode == 0, completed.stderr.decode()[-2000:]
    return json.loads(completed.stdout.decode().strip().splitlines()[-1])


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_desktops_own_first_push_lands_on_a_running_cloud(
    bare_application, desktop, tmp_path, shape
):
    node, checkout = desktop
    app = bare_application
    org = app.api(
        "POST", "/organizations/", expected=201, json={"name": "Desktop code " + uuid.uuid4().hex}
    )["id"]
    publish_entitlement(app, org)
    build, tip_parents = SHAPES[shape]
    local = Git.init(tmp_path / "local")
    build(local)
    head = local.text("rev-parse", "HEAD")
    assert len(parents(local, "HEAD")) == tip_parents

    result = run_desktop(
        node,
        checkout,
        {
            "apiBaseUrl": app.url + "/api/v1",
            "accessToken": app.auth.headers("authenticated")["Authorization"].removeprefix(
                "Bearer "
            ),
            "userId": current_user(app),
            "organizationId": org,
            "rootPath": str(local.path),
            "projectName": f"Desktop {shape}",
            "sourceBranch": "main",
        },
    )
    assert result["ok"] is True, result.get("error")
    state = result["state"]
    assert state["push"] == "accepted" and state["attemptCommitOid"] == head
    project = state["projectId"]
    # Desktop leaves the folder linked: canonical remote and upstream.
    assert urlsplit(result["remote"]).path == f"/git/{project}.git"
    assert result["upstream"] == "puppyone/main"
    assert not (local.path / ".git/puppyone/pending-cloud-publish.v1.json").exists()

    # Read it back as another client with its own credential.
    secret = "pwg_" + secrets.token_urlsafe(32)
    app.api(
        "POST",
        f"/projects/{project}/git-credentials",
        expected=201,
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "target": {"kind": "project_root", "project_id": project},
            "mode": "r",
            "credential": secret,
        },
    )
    cold = Git.init(tmp_path / "cold.git", bare=True)
    cold.run(
        *scoped(result["remote"], f"Authorization: {authorization(secret)}"),
        "fetch",
        result["remote"],
        "+refs/*:refs/*",
    )
    cold.run("fsck", "--full", "--strict")
    assert cold.text("rev-parse", "main") == head
    assert parents(cold, "main") == parents(local, "HEAD")
    assert cold.text("rev-list", "--count", "main") == local.text("rev-list", "--count", "HEAD")
