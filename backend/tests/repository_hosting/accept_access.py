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
from src.version_engine.admission.connector_policy import CLI_FS_KNOWN_COMMANDS
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
            # Credential issuance enables the standard target Surfaces. Exercise
            # their idempotent management endpoint and the actual CLI policy.
            file_headers = {"X-Access-Key": credential, "X-Puppy-Client": "cli"}
            access_prefix = f"/api/v1/projects/{entry.project}/access/surfaces"
            enabled = await http.post(
                access_prefix + "/enable-target",
                headers=contract,
                json={"target": {"kind": "project_root", "project_id": entry.project}},
            )
            assert enabled.status_code == 200, enabled.text
            cli_surface = next(row for row in enabled.json()["data"] if row["kind"] == "cli")
            cli_surface_url = access_prefix + "/" + cli_surface["id"]

            assert (await cli("cat", "readme.md")).stdout == "from Git\n"
            await cli("write", "readme.md", "--content", "from CLI\n", "--type", "markdown")
            current = (await http.get(prefix + "/cat", params={"path": "readme.md"})).json()["data"]
            assert current["content_text"] == "from CLI\n"
            await asyncio.to_thread(git.run, "fetch", "origin", "main")
            assert (
                await asyncio.to_thread(git.run, "show", "FETCH_HEAD:readme.md")
            ).stdout == b"from CLI\n"

            # Management changes must invalidate the already-used admission cache.
            paused = await http.post(cli_surface_url + "/pause", headers=contract)
            assert paused.status_code == 200, paused.text
            denied = await http.get(
                "/api/v1/ap-fs/cat", params={"path": "readme.md"}, headers=file_headers
            )
            assert denied.status_code == 403 and "paused" in denied.text, denied.text
            # CLI pause is scoped to its own channel, not the project's Git remote.
            await asyncio.to_thread(git.run, "ls-remote", "origin")
            resumed = await http.post(cli_surface_url + "/resume", headers=contract)
            assert resumed.status_code == 200, resumed.text
            assert (await cli("cat", "readme.md")).stdout == "from CLI\n"

            restricted = await http.patch(
                cli_surface_url,
                headers=contract,
                json={"policy": {"fs": {"allowed_commands": ["cat", "stat"]}}},
            )
            assert restricted.status_code == 200, restricted.text
            denied = await http.post(
                "/api/v1/ap-fs/write",
                headers=file_headers,
                json={
                    "path": "readme.md",
                    "content": "policy must reject this",
                    "node_type": "markdown",
                },
            )
            assert denied.status_code == 403 and "CLI_FS_COMMAND_DENIED" in denied.text, denied.text
            restored = await http.patch(cli_surface_url, headers=contract, json={"policy": {}})
            assert restored.status_code == 200, restored.text
            denied = await http.post(
                "/api/v1/ap-fs/rm", headers=file_headers, json={"path": "readme.md"}
            )
            assert denied.status_code == 403 and "CLI_FS_COMMAND_DENIED" in denied.text, denied.text

            readonly_token = "pwg_" + secrets.token_urlsafe(32)
            readonly = await http.post(
                f"/api/v1/projects/{entry.project}/git-credentials",
                headers={**contract, "Idempotency-Key": str(uuid4())},
                json={
                    "target": {"kind": "project_root", "project_id": entry.project},
                    "mode": "r",
                    "credential": readonly_token,
                },
            )
            assert readonly.status_code == 201, readonly.text
            readonly_headers = {**file_headers, "X-Access-Key": readonly_token}
            assert (
                await http.get(
                    "/api/v1/ap-fs/cat", params={"path": "readme.md"}, headers=readonly_headers
                )
            ).status_code == 200
            denied = await http.post(
                "/api/v1/ap-fs/write",
                headers=readonly_headers,
                json={
                    "path": "readme.md",
                    "content": "readonly must reject this",
                    "node_type": "markdown",
                },
            )
            assert denied.status_code == 403, denied.text
            assert (await cli("cat", "readme.md")).stdout == "from CLI\n"

            async def fs(method, operation, **kwargs):
                response = await http.request(
                    method, "/api/v1/ap-fs/" + operation, headers=file_headers, **kwargs
                )
                assert response.status_code == 200, response.text
                return response.json()["data"]

            # Cover the native operation adapter with real refs/objects, not
            # mocked filesystem methods. Enable deletion explicitly for cleanup.
            allowed = await http.patch(
                cli_surface_url,
                headers=contract,
                json={"policy": {"fs": {"allowed_commands": sorted(CLI_FS_KNOWN_COMMANDS)}}},
            )
            assert allowed.status_code == 200, allowed.text
            await fs("POST", "mkdir", json={"path": "checks"})
            await fs("POST", "touch", json={"paths": ["checks/empty.txt", "checks/spare.txt"]})
            await fs("POST", "cp", json={"old_path": "readme.md", "new_path": "checks/copy.md"})
            await fs(
                "POST", "mv", json={"old_path": "checks/copy.md", "new_path": "checks/moved.md"}
            )
            assert (await cli("cat", "checks/moved.md")).stdout == "from CLI\n"
            assert not (await fs("GET", "stat", params={"path": "checks/copy.md"}))["exists"]
            for operation in ("ls", "tree"):
                result = await fs("GET", operation, params={"path": "checks", "include_size": True})
                assert {item["path"] for item in result["entries"]} == {
                    "checks/empty.txt",
                    "checks/spare.txt",
                    "checks/moved.md",
                }
            found = await fs("GET", "find", params={"path": "checks", "name": "*.md"})
            assert [item["path"] for item in found["entries"]] == ["checks/moved.md"]
            matches = await fs("GET", "grep", params={"path": "checks", "pattern": "from CLI"})
            assert matches["matched_files"] == 1 and matches["returned_count"] == 1

            binary = bytes(range(256)) * 8
            uploaded = await http.post(
                "/api/v1/ap-fs/upload",
                params={"path": "checks/data.bin"},
                headers={**file_headers, "Content-Type": "application/octet-stream"},
                content=binary,
            )
            assert uploaded.status_code == 200, uploaded.text
            for query, expected in [({}, binary), ({"start": 253, "limit": 16}, binary[253:269])]:
                downloaded = await http.get(
                    "/api/v1/ap-fs/raw",
                    params={"path": "checks/data.bin", **query},
                    headers=file_headers,
                )
                assert downloaded.status_code == 200 and downloaded.content == expected
                assert int(downloaded.headers["X-Puppyone-Size"]) == len(binary)
            # Reject traversal and stale AP-FS writes without changing the file.
            invalid = await http.get(
                "/api/v1/ap-fs/cat", params={"path": "../readme.md"}, headers=file_headers
            )
            assert invalid.status_code == 400, invalid.text
            unchanged_head = (await fs("GET", "stat", params={"path": ""}))["head_commit_id"]
            for operation, body in [
                ("write", {"path": "readme.md", "content": "stale CLI", "node_type": "markdown"}),
                ("mkdir", {"path": "checks/stale-dir"}),
                ("touch", {"path": "checks/stale.txt"}),
                ("touch", {"path": "checks/empty.txt"}),
                ("cp", {"old_path": "readme.md", "new_path": "checks/stale.md"}),
                ("mv", {"old_path": "readme.md", "new_path": "checks/stale.md"}),
                ("rm", {"path": "readme.md"}),
            ]:
                stale = await http.post(
                    "/api/v1/ap-fs/" + operation,
                    headers=file_headers,
                    json={**body, "base_commit_id": commit},
                )
                assert stale.status_code == 409, (operation, stale.text)
            stale = await http.post(
                "/api/v1/ap-fs/upload",
                headers=file_headers,
                params={"path": "checks/data.bin", "base_commit_id": commit},
                content=b"stale",
            )
            assert stale.status_code == 409, stale.text
            assert (await fs("GET", "stat", params={"path": ""}))[
                "head_commit_id"
            ] == unchanged_head
            assert (await cli("cat", "readme.md")).stdout == "from CLI\n"
            await asyncio.to_thread(git.run, "fetch", "origin", "main")
            assert (
                await asyncio.to_thread(git.run, "show", "FETCH_HEAD:checks/data.bin")
            ).stdout == binary
            await fs(
                "POST",
                "rm",
                json={
                    "paths": [
                        "checks/empty.txt",
                        "checks/spare.txt",
                        "checks/moved.md",
                        "checks/data.bin",
                    ]
                },
            )
            # Git-native mkdir persists a visible .keep placeholder. rmdir
            # must not silently remove that tracked file (or other hidden data).
            hidden = await fs("GET", "ls", params={"path": "checks", "include_hidden": True})
            assert [item["path"] for item in hidden["entries"]] == ["checks/.keep"]
            nonempty = await http.post(
                "/api/v1/ap-fs/rmdir", headers=file_headers, json={"path": "checks"}
            )
            assert nonempty.status_code == 400 and "not empty" in nonempty.text, nonempty.text
            await fs("POST", "rm", json={"path": "checks", "recursive": True})
            assert not (await fs("GET", "stat", params={"path": "checks"}))["exists"]
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
