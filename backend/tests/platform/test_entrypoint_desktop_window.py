"""Current Desktop routes and Space ownership against local canonical services.

Opt-in E2E gate: launch Electron with temporary userData and a loopback server.
Automation is intentionally unregistered; stale flags must not restore it.
Identity and backend facts are isolated. This is not a production, PostgreSQL,
Provider or signed-installer receipt.
"""

import json
import os
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

from src.common_schemas import ApiResponse
from tests.platform.test_synchronize_clients_http import http_server as http_server
from tests.platform.test_synchronize_identity_http import (
    environment as environment,
)
from tests.platform.test_synchronize_public_api import (
    canonical as resource_environment,  # noqa: F401 -- pytest fixture registration
)

pytestmark = pytest.mark.e2e


@pytest.fixture
def canonical(request, monkeypatch):
    app, bindings, runs, queue = request.getfixturevalue("resource_environment")
    # The real source service/PDP/URL provider admission remain mounted. Old
    # resource routes must not be needed by the application window.
    app.router.routes = [
        r for r in app.routes if not getattr(r, "path", "").startswith("/api/v1/integrations")
    ]
    state = {"requests": [], "fail_list": False}
    app.state.window_probe = state
    project = {
        "id": "project-1",
        "name": "Window acceptance",
        "description": None,
        "org_id": "org-1",
        "effective_role": "admin",
        "capabilities": [
            "project.read",
            "project.manage",
            "synchronize.manage",
            "access_surface.manage",
            "history.read",
        ],
    }
    target = {"kind": "project_root", "project_id": "project-1"}

    @app.middleware("http")
    async def record(request, call_next):
        if request.url.path.startswith("/api/v1"):
            state["requests"].append(
                {"method": request.method, "path": request.url.path, "query": request.url.query}
            )
            if request.url.path != "/api/v1/auth/config":
                assert request.headers.get("authorization") == "Bearer isolated-window-access"
        if (
            state["fail_list"]
            and request.method == "GET"
            and request.url.path == "/api/v1/synchronize/bindings"
        ):
            return JSONResponse(
                status_code=503, content={"detail": "Window fixture inventory unavailable"}
            )
        return await call_next(request)

    @app.get("/api/v1/auth/config")
    def auth_config(request: Request):
        import base64

        role = base64.urlsafe_b64encode(b'{"role":"anon"}').decode().rstrip("=")
        return ApiResponse.success(
            {
                "supabase_url": str(request.base_url).rstrip("/"),
                "supabase_anon_key": f"header.{role}.signature",
            }
        )

    @app.post("/auth/v1/token")
    async def refresh(request: Request):
        assert request.query_params["grant_type"] == "refresh_token"
        assert (await request.json())["refresh_token"] == "isolated-window-refresh"
        return {
            "access_token": "isolated-window-access",
            "refresh_token": "isolated-window-refresh",
            "user": {"id": "user-1", "email": "window@example.test"},
            "expires_in": 3600,
        }

    @app.post("/api/v1/auth/initialize")
    def initialize():
        return ApiResponse.success({})

    @app.post("/api/v1/projects/project-1/repository-context")
    async def context(request: Request):
        assert (await request.json())["target"] == target
        return ApiResponse.success({"project": project, "target": target, "scope": None})

    @app.get("/api/v1/projects/project-1")
    def get_project():
        return ApiResponse.success(project)

    @app.get("/api/v1/projects/")
    def projects():
        return ApiResponse.success([project])

    @app.get("/api/v1/projects/project-1/dashboard/resources")
    def dashboard():
        return ApiResponse.success(
            {
                "project": project,
                "nodes": {"total": 0, "folders": 0, "files": 0},
                "resources": [
                    {
                        "resource_kind": "synchronize",
                        "resource_id": b.id,
                        "project_id": b.project_id,
                        "provider": b.provider,
                        "path": b.path,
                        "status": b.status,
                        "name": b.provider,
                        "usage_buckets": [0] * 14,
                    }
                    for b in bindings.items.values()
                ],
                "tools": [],
                "uploads": [],
            }
        )

    @app.get("/api/v1/content/project-1/ls")
    def content():
        return ApiResponse.success({"project_id": "project-1", "path": "", "entries": []})

    @app.get("/api/v1/projects/project-1/scopes")
    @app.get("/api/v1/projects/project-1/access/surfaces")
    @app.get("/api/v1/mcp-endpoints")
    @app.get("/api/v1/organizations/")
    def empty_metadata():
        return ApiResponse.success([])

    @app.get("/api/v1/projects/project-1/access-point")
    def identity(request: Request):
        return ApiResponse.success(
            {
                "project_id": "project-1",
                "target": target,
                "git_url": str(request.base_url).rstrip("/") + "/git/project-1.git",
            }
        )

    @app.get("/__window/state")
    def snapshot():
        return {
            "bindings": [asdict(b) for b in bindings.items.values()],
            "runs": [
                r.model_dump() if hasattr(r, "model_dump") else asdict(r)
                for r in runs.items.values()
            ],
            **state,
        }

    @app.post("/__window/fail-run")
    def fail_run():
        assert len(bindings.items) == 1 and len(runs.items) == 1
        binding = next(iter(bindings.items.values()))
        binding.status = "error"
        binding.error_message = "Isolated Provider failure for window retry"
        run = next(iter(runs.items.values()))
        run.status = "failed"
        run.error = binding.error_message
        run.result_summary = "Window first run failed"
        return {"binding_id": binding.id, "run_id": run.id}

    @app.post("/__window/inventory/{available}")
    def availability(available: str):
        state["fail_list"] = available != "up"
        return {"ok": True}

    def forbidden(*args, **kwargs):
        raise AssertionError("External Python HTTP is forbidden during window acceptance")

    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)
    return app, bindings, runs, queue


def test_retired_automation_stays_unregistered_after_real_window_reload(
    http_server, canonical, tmp_path
):
    source = os.environ.get("PUPPYONE_DESKTOP_SOURCE")
    assert source, (
        "Set PUPPYONE_DESKTOP_SOURCE to the Desktop worktree for this explicit window gate"
    )
    root = Path(source).resolve()
    driver = root / "tests/e2e/cloud/entrypoint-window.smoke.mjs"
    assert driver.is_file()
    try:
        result = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                "import electron from 'electron'; import {spawn} from 'node:child_process'; "
                "const child=spawn(electron,[process.argv[1]],{stdio:'inherit',env:process.env}); "
                "child.on('exit',(code)=>process.exit(code??1));",
                str(driver),
            ],
            cwd=root,
            env={
                **os.environ,
                "PUPPYONE_ENTRYPOINT_TEST_ORIGIN": http_server,
                "PUPPYONE_ENTRYPOINT_TEST_REPORT": str(tmp_path / "window-report.json"),
                "PUPPYONE_ENTRYPOINT_TEST_WORKSPACE": str(tmp_path / "desktop-runtime"),
            },
            capture_output=True,
            text=True,
            timeout=240,
        )
    finally:
        # Production Main may flush files at process exit, after the driver's
        # first cleanup. The parent owns this disposable directory only.
        shutil.rmtree(tmp_path / "desktop-runtime", ignore_errors=True)
    assert not (tmp_path / "desktop-runtime").exists()
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "window-report.json").read_text())
    assert report["ok"] and report["window_reloads"] == 2
    assert len(set(report["document_ids"])) == report["window_reloads"]
    _, bindings, runs, queue = canonical
    assert report["automation_route_registered"] is False
    assert not bindings.items and not runs.items
    assert queue.enqueue_sync_run.call_count == 0
    assert not any(
        "/integrations" in r["path"] or "/connectors" in r["path"]
        for r in canonical[0].state.window_probe["requests"]
    )


def test_space_navigation_scopes_home_settings_and_windows(http_server, canonical, tmp_path):
    """Production Main/preload/App; owned backend facts, two real windows."""
    app = canonical[0]
    app.router.routes = [
        r
        for r in app.routes
        if getattr(r, "path", "") not in {"/api/v1/organizations/", "/api/v1/projects/"}
    ]
    spaces = [
        {"id": "org-1", "name": "Studio", "slug": "studio", "kind": "space", "is_default": True},
        {
            "id": "org-2",
            "name": "Research",
            "slug": "research",
            "kind": "space",
            "is_default": False,
        },
    ]
    for space in spaces:
        space.update(role="owner", can_manage_space=True, can_create_projects=True)
    first = {
        "id": "project-1",
        "name": "Window acceptance",
        "org_id": "org-1",
        "effective_role": "admin",
        "capabilities": ["project.read", "project.manage"],
    }
    second = {**first, "id": "project-b", "name": "Space B only", "org_id": "org-2"}
    app.add_api_route("/api/v1/organizations/", lambda: ApiResponse.success(spaces))
    app.add_api_route(
        "/api/v1/auth/default-space", lambda: ApiResponse.success(spaces[0]), methods=["POST"]
    )
    app.add_api_route("/api/v1/projects/", lambda: ApiResponse.success([first, second]))
    app.add_api_route("/api/v1/projects/project-b", lambda: ApiResponse.success(second))
    app.add_api_route(
        "/api/v1/projects/project-b/repository-context",
        lambda: ApiResponse.success(
            {
                "project": second,
                "target": {"kind": "project_root", "project_id": "project-b"},
                "scope": None,
            }
        ),
        methods=["POST"],
    )
    app.add_api_route(
        "/api/v1/organizations/org-1/members",
        lambda: ApiResponse.success(
            [{"user_id": "user-1", "role": "owner", "display_name": "Owner"}]
        ),
    )
    app.add_api_route(
        "/api/v1/organizations/org-1/access",
        lambda: ApiResponse.success(
            {"org_id": "org-1", "user_id": "user-1", "role": "owner", "can_manage_billing": True}
        ),
    )
    # Synthetic billing facts prove that Plan renders usage, rather than only
    # displaying an error while the surrounding navigation happens to work.
    @app.get("/api/v1/billing/catalog")
    def billing_catalog():
        features = dict.fromkeys(("access_surface.agent", "access_surface.mcp",
            "access_surface.sandbox", "automation.hosted", "remote_workspace.create",
            "scope_sandbox.connect"), True)
        plans = []
        for plan_id, name, cents, units in (("free", "Free", 0, 300),
                ("team_v2", "Team", 2000, 2400), ("enterprise_v2", "Enterprise", 4000, 2400)):
            plans.append(dict(id=plan_id, aliases=[], name=name, description="Owned window fixture",
                public=True, purchasable=cents > 0, highlighted=False, currency="USD",
                interval="month" if cents else "none", price_per_seat_cents=cents,
                seats=dict(minimum=1, maximum=None), features=features, allow={},
                runtime=dict(fixed_units=units, units_per_seat=0),
                fixed_limits={"projects.max": 20, "storage.max_bytes": 1000000000,
                              "history.retention_days": 30}, per_seat_limits={}))
        return ApiResponse.success(dict(schema_version="1.1", catalog_version="window-1",
            effective_at="2026-11-01T00:00:00Z", currency="USD", plans=plans, add_ons=[],
            runtime=dict(top_ups_enabled=False, overage_enabled=False, unit_seconds=60,
                minimum_units=1, overage_price_cents_per_unit=0, profiles=[], top_up_packs=[])))

    @app.get("/api/v1/billing/organizations/{org_id}/summary")
    def billing_summary(org_id: str):
        assert org_id in {"org-1", "org-2"}
        consumed = 120 if org_id == "org-2" else 60
        return ApiResponse.success(dict(org_id=org_id, plan_id="free", status="free",
            seat_quantity=1, pending_plan_id=None, cancel_at_period_end=False,
            current_period_end=None, catalog_version="window-1", source_revision=1,
            portal_available=False, seat_changes_available=False, runtime_available_units=300-consumed,
            runtime_reserved_units=0, runtime_overage_enabled=False,
            runtime_monthly_limit_cents=0, add_ons=[]))

    @app.get("/api/v1/billing/organizations/{org_id}/usage")
    def billing_usage(org_id: str):
        assert org_id in {"org-1", "org-2"}
        consumed = 120 if org_id == "org-2" else 60
        return ApiResponse.success(dict(runtime=dict(org_id=org_id, available_units=300-consumed,
            reserved_units=0, granted_units=300, consumed_units=consumed,
            postpaid_available_units=0, postpaid_consumed_units=0, buckets=[],
            allowance=dict(granted_units=300, consumed_units=consumed, reserved_units=0,
                resets_at="2026-12-01T00:00:00Z", unit_seconds=60)),
            storage=dict(logical_bytes=100, limit_bytes=1000000000, percent=0,
                threshold_percent=0, version=1)))

    @app.get("/api/v1/organizations/org-2/access")
    def research_access():
        return ApiResponse.success(dict(org_id="org-2", user_id="user-1", role="owner",
            can_manage_billing=True))

    root = Path(os.environ["PUPPYONE_DESKTOP_SOURCE"]).resolve()
    try:
        result = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                "import electron from 'electron';import {spawn} from 'node:child_process';"
                "const child=spawn(electron,[process.argv[1]],{stdio:'inherit',env:process.env});"
                "child.on('exit',(code)=>process.exit(code??1));",
                str(root / "tests/e2e/cloud/space-switching.smoke.mjs"),
            ],
            cwd=root,
            env={
                **os.environ,
                "PUPPYONE_ENTRYPOINT_TEST_ORIGIN": http_server,
                "PUPPYONE_ENTRYPOINT_TEST_REPORT": str(tmp_path / "space-report.json"),
                "PUPPYONE_ENTRYPOINT_TEST_WORKSPACE": str(tmp_path / "space-runtime"),
            },
            capture_output=True,
            text=True,
            timeout=240,
        )
    finally:
        shutil.rmtree(tmp_path / "space-runtime", ignore_errors=True)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "space-report.json").read_text())
    assert report["ok"] and report["window_count"] == 2
    assert report["current_spaces"] == ["org-1", "org-2"]
