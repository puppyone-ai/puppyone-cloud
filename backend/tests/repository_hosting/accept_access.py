"""Explicit acceptance: real HTTP/Web/Git/CLI over owned PostgreSQL and S3.

Reuse the isolated production-entry fixture: local signed JWTs, fixture identity
and repository enrollment, production authorization and default composition.
No hosted database, Supabase login, compute or model is exercised here.
"""

import asyncio
import base64
import os
import secrets
import subprocess
from uuid import uuid4

import pytest

from src.config import settings
from tests.agent.workbench_acceptance.accept_fly import http_server
from tests.agent.workbench_acceptance.accept_production_entry import (
    control_api as control_api,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    entry as entry,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    object_endpoint as object_endpoint,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    postgres as postgres,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    services as services,
)
from tests.repository_hosting.harness.git import Git
from tests.support.cli_binary import cli_command

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("include_cli", [False, True], ids=["web-git", "web-git-cli"])
async def test_web_git_cli_share_native_refs_and_credential_revocation(
    entry, tmp_path, monkeypatch, include_cli
):
    monkeypatch.setattr(settings, "PUBLIC_URL", "")
    monkeypatch.setenv("PUPPYONE_REQUIRE_CLI", "1")
    # The reused billing fixture deliberately allows only ten bytes. Enroll a
    # realistic owned-test quota; leave the production admission checks active.
    entry.postgres.sql(f"""
        UPDATE organization_entitlements
        SET entitlements=jsonb_set(entitlements, '{{limits,storage.max_bytes}}', '65536')
        WHERE org_id='{entry.org}'
    """)
    contract = {"X-PuppyOne-Repository-Contract": "2"}
    credential = "pwg_" + secrets.token_urlsafe(32)
    async with http_server(entry.app, entry.http.headers) as http:
        prefix = f"/api/v1/content/{entry.project}"
        response = await http.post(
            f"/api/v1/projects/{entry.project}/git-credentials",
            headers={**contract, "Idempotency-Key": str(uuid4())},
            json={
                "target": {"kind": "project_root", "project_id": entry.project},
                "mode": "rw",
                "credential": credential,
            },
        )
        assert response.status_code == 201, response.text
        issued = response.json()["data"]
        remote = issued["remote"]["url"]
        assert remote == str(http.base_url).rstrip("/") + f"/git/{entry.project}.git"
        assert credential not in remote

        response = await http.post(
            prefix + "/write",
            json={
                "path": "draft.md",
                "content": "from Web\n",
                "node_type": "markdown",
            },
        )
        assert response.status_code == 200, response.text
        response = await http.post(
            prefix + "/mv",
            json={
                "old_path": "draft.md",
                "new_path": "readme.md",
            },
        )
        assert response.status_code == 200, response.text
        before = (await http.get(prefix + "/cat", params={"path": "readme.md"})).json()["data"]
        assert before["content_text"] == "from Web\n"

        git = await asyncio.to_thread(Git.init, tmp_path / "stock-git")
        auth = "Basic " + base64.b64encode(("x:" + credential).encode()).decode()
        await asyncio.to_thread(git.run, "config", "http.extraHeader", "Authorization: " + auth)
        await asyncio.to_thread(git.run, "remote", "add", "origin", remote)
        await asyncio.to_thread(git.run, "fetch", "origin", "main")
        await asyncio.to_thread(git.run, "checkout", "-B", "main", "FETCH_HEAD")
        assert (git.path / "readme.md").read_bytes() == b"from Web\n"
        assert not (git.path / "draft.md").exists()
        commit = await asyncio.to_thread(git.commit, {"readme.md": b"from Git\n"})
        await asyncio.to_thread(git.run, "push", "origin", "main")
        current = (await http.get(prefix + "/cat", params={"path": "readme.md"})).json()["data"]
        assert current["content_text"] == "from Git\n" and current["head_commit_id"] == commit
        stale = await http.post(
            prefix + "/write",
            json={
                "path": "readme.md",
                "content": "stale",
                "node_type": "markdown",
                "native": {
                    "request_key": str(uuid4()),
                    "repository_revision": before["repository_revision"],
                },
            },
        )
        assert stale.status_code == 409, stale.text

        async def cli(*args, check=True):
            env = {**os.environ, "PUPPYONE_ACCESS_KEY": credential}
            command = cli_command("fs", "--api-url", str(http.base_url), *args)
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                env=env,
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if check:
                assert result.returncode == 0, result.stderr.replace(credential, "[redacted]")
            return result

        if include_cli:
            assert (await cli("cat", "readme.md")).stdout == "from Git\n"
            await cli("write", "readme.md", "--content", "from CLI\n", "--type", "markdown")
            current = (await http.get(prefix + "/cat", params={"path": "readme.md"})).json()["data"]
            assert current["content_text"] == "from CLI\n"
            await asyncio.to_thread(git.run, "fetch", "origin", "main")
            assert (
                await asyncio.to_thread(git.run, "show", "FETCH_HEAD:readme.md")
            ).stdout == b"from CLI\n"
        await asyncio.to_thread(git.run, "fsck", "--full", "--strict")
        response = await http.post(prefix + "/rm", json={"path": "readme.md"})
        assert response.status_code == 200, response.text
        if include_cli:
            assert (await cli("cat", "readme.md", check=False)).returncode != 0
        await asyncio.to_thread(git.run, "fetch", "origin", "main")
        assert (
            await asyncio.to_thread(git.run, "cat-file", "-e", "FETCH_HEAD:readme.md", check=False)
        ).returncode != 0

        response = await http.delete(
            f"/api/v1/projects/{entry.project}/git-credentials/{issued['id']}",
            headers=contract,
        )
        assert response.status_code == 200, response.text
        assert (
            await asyncio.to_thread(git.run, "ls-remote", "origin", check=False)
        ).returncode != 0
        if include_cli:
            assert (await cli("cat", "seed.txt", check=False)).returncode != 0
        assert (await http.get(prefix + "/cat", params={"path": "seed.txt"})).status_code == 200
        assert (
            await http.get(
                prefix + "/cat", params={"path": "seed.txt"}, headers={"Authorization": ""}
            )
        ).status_code == 401
