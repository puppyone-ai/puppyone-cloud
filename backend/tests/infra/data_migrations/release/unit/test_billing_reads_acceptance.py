"""Plan readiness requires the owner's real summary and usage read contracts."""

import copy

import httpx
import pytest

from src.infra.data_migrations.release import acceptance


def responses():
    return {
        "/billing/catalog": {"plans": [{"id": "free"}]},
        "/billing/organizations/org/summary": {"org_id": "org", "plan_id": "free"},
        "/billing/organizations/org/usage": {"runtime": {
            "org_id": "org", "available_units": 120, "reserved_units": 0, "consumed_units": 10,
        }},
    }


def test_plan_reads_are_read_only_and_return_sanitized_evidence():
    seen, bodies = [], responses()

    def call(method, path):
        seen.append((method, path))
        return bodies[path]

    assert acceptance.accept_billing_reads(call, "org") == {
        "billing_catalog_read": True, "billing_summary_read": True, "billing_usage_read": True,
    }
    assert seen == [("GET", path) for path in bodies]


@pytest.mark.parametrize("broken", ["catalog", "summary_owner", "plan", "usage_owner", "usage_units"])
def test_incomplete_or_wrong_owner_account_cannot_pass_plan_read_acceptance(broken):
    bodies = copy.deepcopy(responses())
    if broken == "catalog":
        bodies["/billing/catalog"]["plans"] = []
    elif broken == "summary_owner":
        bodies["/billing/organizations/org/summary"]["org_id"] = "other"
    elif broken == "plan":
        bodies["/billing/organizations/org/summary"]["plan_id"] = ""
    elif broken == "usage_owner":
        bodies["/billing/organizations/org/usage"]["runtime"]["org_id"] = "other"
    else:
        del bodies["/billing/organizations/org/usage"]["runtime"]["available_units"]
    with pytest.raises(RuntimeError, match="incomplete account"):
        acceptance.accept_billing_reads(lambda method, path: bodies[path], "org")


@pytest.mark.parametrize("summary_status", [200, 503])
def test_hosted_probe_uses_normal_login_bearer_and_fails_before_mutations(monkeypatch, summary_status):
    seen, bodies = [], responses()

    def respond(request):
        path = request.url.path.removeprefix("/api/v1")
        seen.append((request.method, path))
        if path == "/auth/login":
            return httpx.Response(200, json={"access_token": "owned-test-login"})
        assert request.headers["Authorization"] == "Bearer owned-test-login"
        assert "X-Internal-Secret" not in request.headers
        if path == "/ai/trial":
            return httpx.Response(200, json={})
        if path.endswith("/summary"):
            return httpx.Response(summary_status, json=bodies[path])
        if path in bodies:
            return httpx.Response(200, json=bodies[path])
        return httpx.Response(500)

    client = httpx.Client
    monkeypatch.setattr(acceptance.httpx, "Client", lambda **kw: client(
        transport=httpx.MockTransport(respond), **kw,
    ))
    with pytest.raises(RuntimeError, match="Release acceptance HTTP"):
        acceptance.accept({"api_url": "https://cloud.example.test", "email": "owner@example.test",
                           "password": "fixture", "org_id": "org", "billing_reads": True})
    assert all(method == "GET" for method, path in seen if path.startswith("/billing/"))
    assert (("POST", "/projects/") in seen) is (summary_status == 200)
