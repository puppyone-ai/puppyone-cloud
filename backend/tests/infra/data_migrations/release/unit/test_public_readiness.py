"""A ready provider deployment can still have an unavailable public edge."""

import httpx
import pytest

from src.infra.data_migrations.release import acceptance


class Clock:
    def __init__(self):
        self.now = 0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


@pytest.mark.parametrize("initial", [502, 503, 504, "connect", "timeout"])
def test_waits_for_public_edge_after_provider_reports_success(initial):
    clock, seen = Clock(), []

    def respond(request):
        seen.append((request.method, request.url.path))
        if len(seen) == 1:
            if initial == "connect":
                raise httpx.ConnectError("connection refused", request=request)
            if initial == "timeout":
                raise httpx.ReadTimeout("edge timed out", request=request)
            return httpx.Response(initial)
        return httpx.Response(200, json={"status": "ready"})

    with httpx.Client(base_url="https://cloud.example", transport=httpx.MockTransport(respond)) as client:
        acceptance.wait_api_ready(client, clock=clock, sleep=clock.sleep)
    assert seen == [("GET", "/health"), ("GET", "/health")]
    assert clock.waits == [2]


def test_unavailable_edge_has_a_deadline_and_never_sends_credentials():
    clock, seen = Clock(), []

    def respond(request):
        seen.append(request)
        return httpx.Response(503)

    with (
        httpx.Client(base_url="https://cloud.example", transport=httpx.MockTransport(respond)) as client,
        pytest.raises(TimeoutError, match="before acceptance"),
    ):
        acceptance.wait_api_ready(client, timeout_seconds=3, clock=clock, sleep=clock.sleep)
    assert clock.now == 3
    assert clock.waits == [2, 1]
    assert len(seen) == 2
    assert all(request.method == "GET" and request.url.path == "/health" for request in seen)
    assert all(not request.content and "Authorization" not in request.headers for request in seen)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_real_http_errors_fail_immediately(status):
    clock = Clock()
    with (
        httpx.Client(base_url="https://cloud.example", transport=httpx.MockTransport(
            lambda request: httpx.Response(status),
        )) as client,
        pytest.raises(RuntimeError, match=f"readiness HTTP {status}"),
    ):
        acceptance.wait_api_ready(client, clock=clock, sleep=clock.sleep)
    assert clock.waits == []


@pytest.mark.parametrize("body", [[], {}, {"status": "starting"}])
def test_success_requires_the_actual_api_health_contract(body):
    with (
        httpx.Client(base_url="https://cloud.example", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=body),
        )) as client,
        pytest.raises(RuntimeError, match="did not report ready"),
    ):
        acceptance.wait_api_ready(client)


def test_html_success_is_not_api_readiness():
    with (
        httpx.Client(base_url="https://cloud.example", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>proxy</html>"),
        )) as client,
        pytest.raises(RuntimeError, match="invalid JSON"),
    ):
        acceptance.wait_api_ready(client)


def test_login_is_attempted_once_after_public_readiness(monkeypatch):
    clock, seen = Clock(), []

    def respond(request):
        seen.append((request.method, request.url.path))
        if request.url.path == "/health":
            return httpx.Response(502) if len(seen) == 1 else httpx.Response(200, json={"status": "ready"})
        assert request.url.path == "/api/v1/auth/login"
        return httpx.Response(502)

    original_client, original_wait = httpx.Client, acceptance.wait_api_ready
    monkeypatch.setattr(acceptance.httpx, "Client", lambda **options: original_client(
        transport=httpx.MockTransport(respond), **options,
    ))
    monkeypatch.setattr(acceptance, "wait_api_ready", lambda client: original_wait(
        client, clock=clock, sleep=clock.sleep,
    ))
    with pytest.raises(RuntimeError, match="Release acceptance HTTP 502"):
        acceptance.accept({"api_url": "https://cloud.example", "email": "owner@example.test",
                           "password": "fixture", "org_id": "org"})
    assert seen == [("GET", "/health"), ("GET", "/health"), ("POST", "/api/v1/auth/login")]
