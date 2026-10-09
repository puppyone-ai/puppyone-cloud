"""Production Next browser clients against the same real owned API/DB/S3.

Opt in with PUPPYONE_TEST_ORG_BROWSER=1 after the documented frontend build.
Only Supabase identity endpoints are local substitutes; signed JWTs still pass
the production verifier. Workspace and invitation traffic is never intercepted.
"""

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import Depends

from src.config import settings
from src.platform.auth.dependencies import get_current_user

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PUPPYONE_TEST_ORG_BROWSER") != "1",
        reason="opt-in owned production browser acceptance",
    ),
]
ROOT = Path(__file__).resolve().parents[5]


def test_multi_browser_invitation_and_workspace_revocation(team, monkeypatch, tmp_path):
    t = team
    # This public Auth origin is also the explicit test build's baked-in URL.
    origin = "http://127.0.0.1:45461"
    monkeypatch.setattr(settings, "SUPABASE_PUBLIC_URL", origin)

    @t.app.get("/auth/v1/user")
    def identity(user=Depends(get_current_user)):
        return {
            "id": user.user_id,
            "email": user.email,
            "aud": "authenticated",
            "role": "authenticated",
            "app_metadata": {"provider": "email"},
            "user_metadata": {},
        }

    @t.app.get("/api/v1/auth/methods")
    def methods():
        return {"code": 0, "data": {"providers": ["email"]}}

    # Pre-bind: never attach to somebody else's process on this fixed build port.
    api_socket = socket.socket()
    api_socket.bind(("127.0.0.1", 45461))
    server = uvicorn.Server(uvicorn.Config(t.app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [api_socket]}, daemon=True)
    thread.start()
    web = None
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        web_origin = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "API_INTERNAL_URL": origin,
            "NEXT_PUBLIC_SUPABASE_URL": origin,
            "NEXT_PUBLIC_SUPABASE_ANON_KEY": "owned-acceptance-anon-key",
            "PUPPYONE_NEXT_DIST_DIR": ".next-org-acceptance",
        }
        with (tmp_path / "next.log").open("w") as log:
            web = subprocess.Popen(
                [
                    "node",
                    "node_modules/next/dist/bin/next",
                    "start",
                    "-H",
                    "127.0.0.1",
                    "-p",
                    str(port),
                ],
                cwd=ROOT / "frontend",
                env=env,
                stdout=log,
                stderr=log,
            )
        with httpx.Client(trust_env=False, timeout=1) as probe:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if web.poll() is not None:
                    pytest.fail((tmp_path / "next.log").read_text())
                try:
                    if probe.get(web_origin + "/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("Owned Next server did not become ready")
        fixture = {
            "origin": web_origin,
            "project": t.project,
            "org": t.org,
            "users": t.users,
            "emails": t.emails,
            "tokens": {name: t.token(name) for name in t.users},
            "artifacts": str(tmp_path),
        }
        result = subprocess.run(
            ["node", str(ROOT / "e2e/organization/acceptance.mjs")],
            input=json.dumps(fixture),
            text=True,
            capture_output=True,
            timeout=120,
            env=env,
        )
        (tmp_path / "browser.log").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
        assert '"result":"passed"' in result.stdout
    finally:
        if web:
            web.terminate()
            try:
                web.wait(timeout=10)
            except subprocess.TimeoutExpired:
                web.kill()
                web.wait(timeout=5)
        server.should_exit = True
        thread.join(timeout=10)
        api_socket.close()
        assert not thread.is_alive(), "Owned API did not stop"
