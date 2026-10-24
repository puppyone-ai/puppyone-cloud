"""Sandbox image publication: contract check, idempotent publish and pinning."""

import io
import json
import re
from pathlib import Path

import pytest

from scripts import sandbox_image_release as release

AGENT_WORKER = Path(__file__).resolve().parents[2] / "agent-worker"
BUILD_ID = "a" * 64
DIGEST = "sha256:" + "b" * 64
MANIFEST = {
    "runtime_protocol": 1,
    "file_protocol": 1,
    "capabilities": ["workspace_lifecycle", "native_files", "file_watch", "workspace_shell"],
    "build_id": BUILD_ID,
}


def test_backend_contract_is_read_without_importing_the_application():
    contract = release.expected_contract()
    assert contract["RUNTIME_PROTOCOL"] == 1 and contract["FILE_PROTOCOL"] == 1
    assert {"workspace_lifecycle", "native_files", "file_watch"} <= contract[
        "REQUIRED_CAPABILITIES"
    ]


def test_image_manifest_generator_matches_the_backend_contract():
    """The image declares what this backend requires; a one-sided bump fails CI."""
    source = (AGENT_WORKER / "write-runtime-manifest.mjs").read_text()
    contract = release.expected_contract()
    assert (
        int(re.search(r"runtime_protocol:\s*(\d+)", source).group(1))
        == contract["RUNTIME_PROTOCOL"]
    )
    assert int(re.search(r"file_protocol:\s*(\d+)", source).group(1)) == contract["FILE_PROTOCOL"]
    declared = set(
        re.findall(r"'([a-z_]+)'", re.search(r"capabilities:\s*\[([^\]]*)\]", source).group(1))
    )
    assert contract["REQUIRED_CAPABILITIES"] <= declared


def test_dockerfile_writes_the_manifest_the_release_reads():
    dockerfile = (AGENT_WORKER / "Dockerfile").read_text()
    assert "RUN node write-runtime-manifest.mjs" in dockerfile
    assert "WORKDIR /opt/puppyone-agent" in dockerfile
    assert release.MANIFEST == "/opt/puppyone-agent/runtime-manifest.json"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"runtime_protocol": 2}, "runtime protocol"),
        ({"file_protocol": 0}, "file protocol"),
        ({"capabilities": ["workspace_lifecycle"]}, "lacks required capabilities"),
        ({"build_id": "not-a-hash"}, "build_id"),
        ({"build_id": None}, "build_id"),
    ],
)
def test_incompatible_images_are_refused(change, message):
    with pytest.raises(release.ReleaseError, match=message):
        release.check_manifest({**MANIFEST, **change}, release.expected_contract())


def test_compatible_image_returns_its_build_id():
    assert release.check_manifest(MANIFEST, release.expected_contract()) == BUILD_ID


def test_existing_content_tag_is_reused_never_overwritten(monkeypatch):
    commands = []
    monkeypatch.setattr(release, "remote_digest", lambda reference: DIGEST)
    monkeypatch.setattr(release, "run", lambda *command, **_: commands.append(command) or "")
    reference, pushed = release.publish("local:image", "registry.fly.io/app", BUILD_ID)
    assert (reference, pushed) == (f"registry.fly.io/app@{DIGEST}", False)
    assert commands == []


def test_new_content_is_tagged_by_build_and_pushed(monkeypatch):
    commands, digests = [], iter([None, DIGEST])
    monkeypatch.setattr(release, "remote_digest", lambda reference: next(digests))
    monkeypatch.setattr(release, "run", lambda *command, **_: commands.append(command) or "")
    reference, pushed = release.publish("local:image", "registry.fly.io/app", BUILD_ID)
    tag = f"registry.fly.io/app:build-{BUILD_ID}"
    assert commands == [("docker", "tag", "local:image", tag), ("docker", "push", tag)]
    assert (reference, pushed) == (f"registry.fly.io/app@{DIGEST}", True)


def test_unresolvable_push_fails(monkeypatch):
    monkeypatch.setattr(release, "remote_digest", lambda reference: None)
    monkeypatch.setattr(release, "run", lambda *command, **_: "")
    with pytest.raises(release.ReleaseError, match="digest"):
        release.publish("local:image", "registry.fly.io/app", BUILD_ID)


class FakeRailway:
    def __init__(self, values, *, store=True):
        self.values, self.store, self.pins = dict(values), store, []

    def pinned(self, service):
        return self.values.get(service)

    def pin(self, service, reference):
        self.pins.append((service, reference))
        if self.store:
            self.values[service] = reference


def test_pinning_is_reported_not_configured_without_a_token():
    assert release.pin_all(None, ["api"], "ref") == {"state": "not_configured", "services": {}}


def test_pinning_changes_only_services_that_differ():
    railway = FakeRailway({"api": "old", "worker": "new"})
    result = release.pin_all(railway, ["api", "worker"], "new")
    assert railway.pins == [("api", "new")]
    assert result == {
        "state": "pinned",
        "services": {"api": {"changed": True, "previous": "old"}, "worker": {"changed": False}},
    }


def test_pin_that_does_not_persist_fails():
    with pytest.raises(release.ReleaseError, match="did not store"):
        release.pin_all(FakeRailway({"api": "old"}, store=False), ["api"], "new")


def railway_with(response):
    seen = []

    def opener(request, timeout):
        seen.append(request)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(json.dumps(response).encode())

    return release.Railway("secret-token", "project", "environment", opener=opener), seen


def test_railway_uses_the_environment_scoped_project_token():
    railway, seen = railway_with({"data": {"variables": {"CLOUD_AGENT_FLY_IMAGE": "ref"}}})
    assert railway.pinned("service") == "ref"
    request = seen[0]
    assert request.get_header("Project-access-token") == "secret-token"
    body = json.loads(request.data)
    assert body["variables"] == {"p": "project", "e": "environment", "s": "service"}


@pytest.mark.parametrize(
    "response",
    [
        {"errors": [{"message": "Not Authorized"}]},
        release.urllib.error.HTTPError("url", 401, "Unauthorized", None, None),
    ],
)
def test_railway_failures_never_echo_the_token(response):
    railway, _ = railway_with(response)
    with pytest.raises(release.ReleaseError) as caught:
        railway.pinned("service")
    assert "secret-token" not in str(caught.value)


def test_main_publishes_and_states_that_nothing_was_pinned(monkeypatch, tmp_path, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setattr(release, "read_manifest", lambda image: MANIFEST)
    monkeypatch.setattr(
        release, "publish", lambda image, repository, build_id: (f"{repository}@{DIGEST}", True)
    )
    monkeypatch.delenv("RAILWAY_TOKEN", raising=False)
    monkeypatch.setenv("RAILWAY_SANDBOX_IMAGE_SERVICES", "api,worker")
    assert release.main(["--image", "local:image", "--app", "app", "--summary", str(summary)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert (
        report["image"] == f"registry.fly.io/app@{DIGEST}"
        and report["pin"]["state"] == "not_configured"
    )
    assert "Not pinned" in summary.read_text()


def test_main_pins_configured_services(monkeypatch, tmp_path, capsys):
    railway = FakeRailway({"api": "old", "worker": "old"})
    monkeypatch.setattr(release, "read_manifest", lambda image: MANIFEST)
    monkeypatch.setattr(
        release, "publish", lambda image, repository, build_id: (f"{repository}@{DIGEST}", False)
    )
    monkeypatch.setattr(release, "Railway", lambda *args, **kwargs: railway)
    for key, value in {
        "RAILWAY_TOKEN": "secret-token",
        "RAILWAY_PROJECT_ID": "project",
        "RAILWAY_ENVIRONMENT_ID": "environment",
        "RAILWAY_SANDBOX_IMAGE_SERVICES": "api, worker",
    }.items():
        monkeypatch.setenv(key, value)
    assert (
        release.main(
            ["--image", "local:image", "--app", "app", "--summary", str(tmp_path / "s.md")]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert sorted(railway.pins) == [
        ("api", f"registry.fly.io/app@{DIGEST}"),
        ("worker", f"registry.fly.io/app@{DIGEST}"),
    ]
    assert "secret-token" not in json.dumps(report)
