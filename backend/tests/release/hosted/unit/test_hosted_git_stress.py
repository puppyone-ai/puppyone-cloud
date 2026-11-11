"""Hosted stress must execute every iteration, stay scoped and fail honestly."""

import importlib.util
import json
import stat
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[5]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


stress = load("owned_hosted_git_stress", ROOT / "scripts/testing/hosted_git_stress.py")


def account():
    return {"api_url": "https://staging.example.test", "org_id": "owned-org",
            "email": "acceptance@example.test", "password": "fixture-password"}


def test_only_account_credentials_enter_an_owner_only_file_without_overwrite(tmp_path):
    destination = tmp_path / "private.json"
    config = {"acceptance": account(), "railway_token": "unrelated-private-marker"}
    stress.prepare_account(json.dumps(config), destination)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert json.loads(destination.read_text()) == account()
    assert "unrelated-private-marker" not in destination.read_text()
    with pytest.raises(FileExistsError):
        stress.prepare_account(json.dumps(config), destination)


def handler(seen, fail=None):
    files = {}

    def respond(request):
        path = request.url.path.removeprefix("/api/v1")
        seen.append((request.method, path))
        if path == "/auth/login":
            return httpx.Response(200, json={"access_token": "fresh-owned-session"})
        assert request.headers["Authorization"] == "Bearer fresh-owned-session"
        assert request.headers["X-PuppyOne-Repository-Contract"] == "2"
        if request.method == "DELETE":
            return httpx.Response(503 if fail == "cleanup" else 202, json={})
        if path == "/projects/":
            assert json.loads(request.content)["org_id"] == "owned-org"
            return httpx.Response(503 if fail == "create" else 201,
                                  json={"data": {"id": "owned-project"}})
        assert "owned-project" in path
        if path.endswith("/enable-target"):
            return httpx.Response(200, json={"data": [{"kind": "git_remote"}, {"kind": "cli"}]})
        if path.endswith("/bulk-write"):
            if fail == "write":
                return httpx.Response(503, json={"detail": "private-response-marker"})
            files.update({f["path"]: f["content"] for f in json.loads(request.content)["files"]})
            return httpx.Response(200, json={})
        if path.endswith("/mv"):
            if fail == "move":
                return httpx.Response(503)
            body = json.loads(request.content)
            files[body["new_path"]] = files.pop(body["old_path"])
            return httpx.Response(200, json={})
        if path.endswith("/cat"):
            value = "wrong" if fail == "read" else files[request.url.params["path"]]
            return httpx.Response(200, json={"data": {"content_text": value}})
        if path.endswith("/health"):
            return httpx.Response(200, json={"data": {
                "health": "healthy", "git_usable": fail != "health",
            }})
        raise AssertionError("Unexpected endpoint")

    return respond


def test_all_requested_iterations_finish_on_one_owned_project_and_cleanup():
    seen = []
    result = stress.run(account(), 3, api_url=account()["api_url"],
                        transport=httpx.MockTransport(handler(seen)))
    assert result == {"iterations": 3, "passed_iterations": 3,
                      "git_read_after_move": True, "cleanup_requested": True}
    assert seen.count(("POST", "/projects/")) == 1
    assert seen.count(("POST", "/content/owned-project/bulk-write")) == 3
    assert seen[-1] == ("DELETE", "/projects/owned-project")


@pytest.mark.parametrize("failure", ["create", "write", "move", "read", "health", "cleanup"])
def test_any_failed_setup_write_read_health_or_cleanup_cannot_return_success(failure):
    seen = []
    with pytest.raises(RuntimeError) as error:
        stress.run(account(), 3, api_url=account()["api_url"],
                   transport=httpx.MockTransport(handler(seen, failure)))
    assert "private-response-marker" not in str(error.value)
    assert seen.count(("POST", "/content/owned-project/bulk-write")) <= (3 if failure == "cleanup" else 1)
    assert (("DELETE", "/projects/owned-project") in seen) is (failure != "create")


def test_url_override_cannot_receive_protected_account_credentials():
    seen = []
    with pytest.raises(ValueError, match="match the protected"):
        stress.run(account(), 1, api_url="https://other.example.test",
                   transport=httpx.MockTransport(handler(seen)))
    assert seen == []


def test_default_target_is_derived_from_the_same_protected_account():
    seen = []
    result = stress.run(account(), 1, transport=httpx.MockTransport(handler(seen)))
    assert result["passed_iterations"] == 1


def test_failed_login_reports_only_fixed_check_name_and_http_status():
    def reject(request):
        return httpx.Response(429, json={"detail": "private-login-response"})

    with pytest.raises(stress.HostedCheckFailure) as error:
        stress.run(account(), 1, transport=httpx.MockTransport(reject))
    assert str(error.value) == "login: HTTP 429"
    assert "private-login-response" not in str(error.value)


@pytest.mark.parametrize("iterations", [0, 101, True])
def test_iteration_budget_is_checked_before_contacting_the_host(iterations):
    with pytest.raises(ValueError, match=r"1\.\.100"):
        stress.run(account(), iterations, api_url=account()["api_url"])


def test_legacy_manual_stress_also_counts_project_creation_failures():
    old = load("legacy_manual_smoke_check", ROOT / "backend/scripts/railway_smoke.py")
    smoke = old.Smoke("fixture-jwt")
    smoke.client.close()

    def respond(request):
        if request.url.path.endswith("/organizations/"):
            return httpx.Response(200, json={"data": [{"id": "org"}]})
        return httpx.Response(503, json={})

    with httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(respond)) as client:
        smoke.client = client
        assert smoke.stress_move_health_race(3) == 3


@pytest.mark.parametrize("write_fails", [False, True])
def test_legacy_manual_stress_checks_mutation_responses_and_accepts_async_cleanup(write_fails):
    old = load("legacy_manual_stress_writes", ROOT / "backend/scripts/railway_smoke.py")
    smoke = old.Smoke("fixture-jwt")
    smoke.client.close()
    cleaned = []

    def respond(request):
        if request.method == "DELETE":
            cleaned.append(True)
            return httpx.Response(202, json={})
        if request.url.path.endswith("/organizations/"):
            return httpx.Response(200, json={"data": [{"id": "org"}]})
        if request.url.path.endswith("/projects/"):
            return httpx.Response(201, json={"data": {"id": "owned"}})
        if request.url.path.endswith("/bulk-write") and write_fails:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": {"health": "healthy", "git_usable": True}})

    with httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(respond)) as client:
        smoke.client = client
        if write_fails:
            with pytest.raises(AssertionError, match="HTTP 503"):
                smoke.stress_move_health_race(1)
        else:
            assert smoke.stress_move_health_race(1) == 0
    assert cleaned == [True]
