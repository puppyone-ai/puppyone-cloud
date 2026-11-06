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
from tests.repository_hosting.integration.test_bare_repository_application import (
    bare_application as bare_application,
)
from tests.repository_hosting.integration.test_bare_repository_application import (
    publish_entitlement,
)

pytestmark = [pytest.mark.hosting_application, pytest.mark.hosting_s3]

CONTRACT = Path(__file__).resolve().parents[4] / "contracts" / "cloud-git-acceptance-v1.json"


def commit(client, name, content, message):
    return client.commit({name: content.encode()}, message)


def linear(client):
    commit(client, "README.md", "first\n", "first")
    commit(client, "notes.md", "second\n", "second")


def merge_tip(client):
    commit(client, "README.md", "base\n", "base")
    client.run("checkout", "-q", "-b", "feature")
    commit(client, "feature.md", "feature\n", "feature")
    client.run("checkout", "-q", "main")
    commit(client, "main.md", "main\n", "main")
    client.run(
        "merge", "-q", "--no-ff", "feature", "-m", "Merge pull request #1 from example/feature"
    )


def merge_in_history(client):
    merge_tip(client)
    commit(client, "after.md", "after the merge\n", "after the merge")


def octopus_merge_tip(client):
    commit(client, "README.md", "base\n", "base")
    for name in ("one", "two", "three"):
        client.run("checkout", "-q", "-b", name)
        commit(client, f"{name}.md", f"{name}\n", name)
        client.run("checkout", "-q", "main")
    client.run("merge", "-q", "--no-ff", "one", "two", "three", "-m", "octopus")


def unrelated_histories_merge(client):
    commit(client, "README.md", "base\n", "base")
    client.run("checkout", "-q", "--orphan", "imported")
    client.run("rm", "-rq", "--cached", ".")
    (client.path / "README.md").unlink()
    commit(client, "imported.md", "imported\n", "imported")
    client.run("checkout", "-q", "main")
    client.run(
        "merge",
        "-q",
        "--no-ff",
        "--allow-unrelated-histories",
        "imported",
        "-m",
        "bring in imported",
    )


def empty_snapshot(client):
    client.run("commit", "-q", "--allow-empty", "-m", "Initial snapshot")


SHAPES = {
    "linear": (linear, 1),
    "merge_tip": (merge_tip, 2),
    "merge_in_history": (merge_in_history, 1),
    "octopus_merge_tip": (octopus_merge_tip, 4),
    "unrelated_histories_merge": (unrelated_histories_merge, 2),
    "empty_snapshot": (empty_snapshot, 0),
}


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


def authorization(secret):
    return "Basic " + base64.b64encode(f"x-puppyone-token:{secret}".encode()).decode()


def scoped(remote, *headers):
    """Desktop scopes every header to the canonical remote URL."""
    return [arg for header in headers for arg in ("-c", f"http.{remote}.extraHeader={header}")]


def parents(client, ref):
    return client.text("rev-list", "--parents", "-n", "1", ref).split()[1:]


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
