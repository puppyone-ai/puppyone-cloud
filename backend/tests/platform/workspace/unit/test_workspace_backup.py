"""Unit tests for workspace automatic backup (pause, periodic, boot source, divergence)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.config import settings
from src.platform.repository_policy import reset_policy_cache
from src.platform.workspace.lifecycle import WorkbenchLifecycle
from src.platform.workspace.sync import BACKUP_MESSAGE, SAVE_MESSAGE, WorkspaceGit
from src.version_engine.read.ref_metadata import GitBranchBase, RefState

MAIN = "refs/heads/main"
WORKSPACE_REF = "refs/heads/workspaces/owner"
AGENT_REF = "refs/heads/agents/agent-1"


def _set_policy(monkeypatch, name: str) -> None:
    monkeypatch.setattr(settings, "REPOSITORY_POLICY", name)
    monkeypatch.setattr(settings, "REPOSITORY_POLICY_FILE", "")
    reset_policy_cache()


def _base(ref: str, oid: str | None) -> dict:
    guard = RefState(target=ref.encode()) if ref == MAIN else None
    return GitBranchBase("sha1", 1, ref.encode(), RefState(oid=oid), guard).wire()


class FakeChannel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.connect = AsyncMock()
        self.close = AsyncMock()
        self.git_handler = None

    async def control(self, action, value=None):
        if action == "capabilities":
            return {"workspace_git": 2}
        self.calls.append(value)
        return self.replies.pop(0)


def _make_git(replies, *, refs=None, result=None):
    refs = {WORKSPACE_REF: "a" * 40, MAIN: "b" * 40, AGENT_REF: "d" * 40, **(refs or {})}

    def describe(project, grant, target_ref=None):
        ref = target_ref or MAIN
        return _base(ref, refs[ref])

    channel = FakeChannel(replies)
    transport = SimpleNamespace(
        describe=Mock(side_effect=describe),
        result=Mock(return_value=result or {"status": "committed"}),
        exchange=AsyncMock(return_value={"status": 200}),
        publication=lambda key, selected, candidate: SimpleNamespace(request_key=key),
    )
    service = SimpleNamespace(
        transport=transport,
        context=Mock(return_value=({}, "grant")),
        repository=SimpleNamespace(has_active_runs=Mock(return_value=False)),
        channel_factory=Mock(return_value=channel),
        connection=AsyncMock(return_value="connection"),
    )
    return WorkspaceGit(service), channel, service


def _default_row(**overrides) -> dict:
    now = datetime.now(UTC)
    row = {
        "id": "wb-1",
        "user_id": "owner",
        "project_id": "proj-1",
        "generation": "gen-1",
        "version": 1,
        "compute_epoch": 1,
        "state": "running",
        "is_default": True,
        "agent_registration_id": None,
        "target_ref": WORKSPACE_REF,
        "binding": {"provider": "docker", "artifact": "img"},
        "resource": {
            "provider": "docker",
            "resource_id": "vm-1",
            "initialization": {"base_oid": "b" * 40},
        },
        "saved_oid": "b" * 40,
        "save_finished_at": (now - timedelta(seconds=400)).isoformat(),
        "last_activity_at": (now - timedelta(seconds=100)).isoformat(),
        "unpublished_changes": True,
        "owner_deleted_at": None,
        "open_requested": False,
        "backup_until": None,
        "operation_id": "op-1",
    }
    row.update(overrides)
    return row


def _make_lifecycle(*, git_snapshot=None, repository=None, transport=None, provider_runtime=None):
    runtime = provider_runtime or SimpleNamespace(
        pause_resource=AsyncMock(
            return_value=SimpleNamespace(receipt=lambda _res: {"stopped": True})
        ),
        connect_task=AsyncMock(),
        renew=AsyncMock(),
        aclose=AsyncMock(),
    )
    channel = SimpleNamespace(runtime=runtime, resource={"provider": "docker", "resource_id": "vm-1"})
    repo = repository or SimpleNamespace(
        save_outcome=Mock(),
        progress=Mock(),
        transition=Mock(side_effect=lambda row, state, resource=None: {**row, "state": state}),
        disk_lost=Mock(),
        due=Mock(return_value=[]),
        idle=Mock(return_value={"acquired": False}),
        backup_claim=Mock(side_effect=lambda row, _interval: {"acquired": True, "workbench": row}),
        get=Mock(return_value=None),
    )
    provider = SimpleNamespace(
        name="docker",
        capabilities=SimpleNamespace(shared_workspace=True, native_files=True),
        validate=Mock(),
        preflight=AsyncMock(),
        aclose=AsyncMock(),
    )
    service = SimpleNamespace(
        repository=repo,
        transport=transport or SimpleNamespace(describe=Mock(), exchange=AsyncMock()),
        filesystem=SimpleNamespace(operation=AsyncMock()),
        billing=SimpleNamespace(
            ensure_session=AsyncMock(),
            check_session=AsyncMock(),
            resume_session=AsyncMock(),
            detach_session=AsyncMock(),
            finish_session=AsyncMock(return_value=True),
        ),
        channel_factory=Mock(return_value=channel),
        providers=Mock(return_value=provider),
        channels=SimpleNamespace(invalidate=AsyncMock(), is_healthy=Mock(return_value=True)),
        check=AsyncMock(),
        connection=AsyncMock(return_value="conn"),
        context=Mock(return_value=({}, SimpleNamespace(org_id="org-1"))),
        git=SimpleNamespace(
            snapshot=git_snapshot or AsyncMock(return_value={"status": "saved", "tip": "c" * 40})
        ),
    )
    return WorkbenchLifecycle(service), service


# ---------------------------------------------------------------------------
# 1. Pause backups
# ---------------------------------------------------------------------------


async def test_pause_backs_up_user_workspace_to_main_in_vm_only_and_skips_in_open(monkeypatch):
    row = _default_row(state="pausing")

    # vm-only: pause snapshots default workspace (WorkspaceGit.snapshot resolves target_ref=main)
    _set_policy(monkeypatch, "vm-only")
    lifecycle, service = _make_lifecycle()
    await lifecycle.pause(row)
    service.git.snapshot.assert_awaited_once_with(row)
    service.repository.save_outcome.assert_called_once_with(
        row, "saved", error=None, oid="c" * 40
    )
    assert any(call.args[1] == "pausing" for call in service.repository.progress.call_args_list)

    # open: user workspace pause does NOT snapshot (backup.on_pause = false)
    _set_policy(monkeypatch, "open")
    lifecycle_open, service_open = _make_lifecycle()
    await lifecycle_open.pause(row)
    service_open.git.snapshot.assert_not_awaited()
    service_open.repository.save_outcome.assert_not_called()


async def test_pause_still_saves_external_agent_to_its_own_branch_in_both_policies(monkeypatch):
    agent_row = _default_row(
        state="pausing",
        is_default=False,
        agent_registration_id="agent-1",
        target_ref=AGENT_REF,
    )
    for policy_name in ("vm-only", "open"):
        _set_policy(monkeypatch, policy_name)
        lifecycle, service = _make_lifecycle()
        await lifecycle.pause(agent_row)
        service.git.snapshot.assert_awaited_once_with(agent_row)
        service.repository.save_outcome.assert_called_once_with(
            agent_row, "saved", error=None, oid="c" * 40
        )


# ---------------------------------------------------------------------------
# 2. Running periodic backup
# ---------------------------------------------------------------------------


async def test_running_backup_requires_interval_and_newer_activity(monkeypatch):
    _set_policy(monkeypatch, "vm-only")
    now = datetime.now(UTC)

    # Case A: interval elapsed (400s >= 300s) AND activity newer -> claims and backs up
    due_row = _default_row(
        state="running",
        save_finished_at=(now - timedelta(seconds=400)).isoformat(),
        last_activity_at=(now - timedelta(seconds=100)).isoformat(),
        unpublished_changes=False,
    )
    lifecycle, service = _make_lifecycle()
    service.repository.due = Mock(return_value=[due_row])
    await lifecycle._reap_provider("docker")
    service.repository.backup_claim.assert_called_once_with(due_row, 300)
    service.git.snapshot.assert_awaited_once_with(due_row)
    service.repository.save_outcome.assert_called_once_with(
        due_row, "saved", error=None, oid="c" * 40
    )
    # Running periodic backup must NOT touch operation progress('pausing')
    service.repository.progress.assert_not_called()

    # Case B: interval NOT elapsed (100s < 300s) -> does not claim or snapshot
    too_soon = _default_row(
        state="running",
        save_finished_at=(now - timedelta(seconds=100)).isoformat(),
        last_activity_at=(now - timedelta(seconds=10)).isoformat(),
        unpublished_changes=True,
    )
    lifecycle2, service2 = _make_lifecycle()
    service2.repository.due = Mock(return_value=[too_soon])
    await lifecycle2._reap_provider("docker")
    service2.repository.backup_claim.assert_not_called()
    service2.git.snapshot.assert_not_awaited()

    # Case C: interval elapsed (500s) but NO activity since last save -> skips
    quiet = _default_row(
        state="running",
        save_finished_at=(now - timedelta(seconds=500)).isoformat(),
        last_activity_at=(now - timedelta(seconds=600)).isoformat(),
        unpublished_changes=False,
    )
    lifecycle3, service3 = _make_lifecycle()
    service3.repository.due = Mock(return_value=[quiet])
    await lifecycle3._reap_provider("docker")
    service3.repository.backup_claim.assert_not_called()
    service3.git.snapshot.assert_not_awaited()

    # Case D: open policy (interval_seconds = 0) -> skips periodic backup
    _set_policy(monkeypatch, "open")
    lifecycle4, service4 = _make_lifecycle()
    service4.repository.due = Mock(return_value=[due_row])
    await lifecycle4._reap_provider("docker")
    service4.repository.backup_claim.assert_not_called()
    service4.git.snapshot.assert_not_awaited()


# ---------------------------------------------------------------------------
# 3. Snapshot target, no-op, first backup on empty main, divergence refusal
# ---------------------------------------------------------------------------


async def test_snapshot_with_no_changes_does_not_push(monkeypatch):
    _set_policy(monkeypatch, "vm-only")
    git, channel, service = _make_git([{"status": "ready", "candidate": "b" * 40}])
    row = _default_row()
    result = await git.snapshot(row)
    assert result == {"status": "saved", "tip": "b" * 40}
    assert [call["action"] for call in channel.calls] == ["snapshot"]
    assert channel.calls[0]["config"]["target_ref"] == MAIN
    assert channel.calls[0]["value"]["message"] == BACKUP_MESSAGE
    assert channel.calls[0]["value"]["include_changed_count"] is True
    service.transport.result.assert_not_called()


async def test_first_backup_on_empty_project_creates_main(monkeypatch, caplog):
    _set_policy(monkeypatch, "vm-only")
    first_oid = "1" * 40
    git, channel, service = _make_git(
        [{"status": "ready", "candidate": first_oid}, {"status": "pushed"}],
        refs={MAIN: None, WORKSPACE_REF: None},
    )
    row = _default_row(saved_oid=None, resource={"initialization": {"base_oid": None}})
    with caplog.at_level(logging.WARNING, logger="src.platform.workspace.sync"):
        result = await git.snapshot(row)
    assert result == {"status": "saved", "tip": first_oid}
    assert [call["action"] for call in channel.calls] == ["snapshot", "push-snapshot"]
    assert channel.calls[0]["config"]["target_ref"] == MAIN
    assert channel.calls[0]["value"]["tip"] is None
    assert channel.calls[1]["config"]["target_ref"] == MAIN
    assert channel.calls[1]["value"] == {"candidate": first_oid}
    service.transport.result.assert_called_once()
    assert not any(rec.message == "workspace_backup_diverged" for rec in caplog.records)


async def test_a_main_that_moved_without_the_workspace_is_never_overwritten(monkeypatch, caplog):
    _set_policy(monkeypatch, "vm-only")
    external_tip = "e" * 40
    git, channel, service = _make_git([], refs={MAIN: external_tip})
    row = _default_row(saved_oid="b" * 40, resource={"initialization": {"base_oid": "b" * 40}})
    with caplog.at_level(logging.WARNING, logger="src.platform.workspace.sync"):
        result = await git.snapshot(row)

    # Nothing is snapshotted or pushed: the Workspace's files would roll main back.
    assert result == {"status": "diverged", "tip": None}
    assert channel.calls == []
    service.transport.result.assert_not_called()
    errors = [rec for rec in caplog.records if rec.message == "workspace_backup_diverged"]
    assert len(errors) == 1
    assert errors[0].levelno == logging.ERROR
    assert errors[0].remote_tip == external_tip
    assert errors[0].saved_oid == "b" * 40


async def test_a_diverged_backup_is_recorded_unsaved_and_not_retried(monkeypatch):
    _set_policy(monkeypatch, "vm-only")
    snapshot = AsyncMock(return_value={"status": "diverged", "tip": None})
    lifecycle, service = _make_lifecycle(git_snapshot=snapshot)
    row = _default_row()
    await lifecycle._save_work(row, on_pause=True)
    snapshot.assert_awaited_once()
    service.repository.save_outcome.assert_called_once_with(
        row, "rejected", error="workbench_backup_diverged", oid=None
    )


async def test_syncing_main_into_the_workspace_lets_its_backups_land_again(monkeypatch):
    _set_policy(monkeypatch, "vm-only")
    external_tip = "e" * 40
    git, channel, service = _make_git([{"status": "merged", "tip": "9" * 40}], refs={MAIN: external_tip})
    service.repository.record_sync = Mock()
    row = _default_row(saved_oid="b" * 40)
    result = await git.sync(row)
    assert result["status"] == "merged"
    service.repository.record_sync.assert_called_once_with(row, external_tip)

    # A conflict leaves main out of the Workspace: nothing is recorded.
    git, channel, service = _make_git([{"status": "conflict", "paths": ["a.md"]}], refs={MAIN: external_tip})
    service.repository.record_sync = Mock()
    await git.sync(row)
    service.repository.record_sync.assert_not_called()

    # Synced, the next backup lands on main on top of what was taken in.
    backup = "f" * 40
    git, channel, _ = _make_git(
        [{"status": "ready", "candidate": backup}, {"status": "pushed"}], refs={MAIN: external_tip}
    )
    result = await git.snapshot(_default_row(saved_oid="b" * 40, synced_oid=external_tip))
    assert result == {"status": "saved", "tip": backup}
    assert [call["action"] for call in channel.calls] == ["snapshot", "push-snapshot"]
    assert channel.calls[0]["value"]["tip"] == external_tip


async def test_external_agent_snapshot_uses_agent_branch_and_save_message(monkeypatch):
    _set_policy(monkeypatch, "vm-only")
    git, channel, _ = _make_git(
        [{"status": "ready", "candidate": "9" * 40}, {"status": "pushed"}],
    )
    agent_row = _default_row(
        is_default=False,
        agent_registration_id="agent-1",
        target_ref=AGENT_REF,
    )
    result = await git.snapshot(agent_row)
    assert result == {"status": "saved", "tip": "9" * 40}
    assert channel.calls[0]["config"]["target_ref"] == AGENT_REF
    assert channel.calls[0]["value"]["message"] == SAVE_MESSAGE
    assert "include_changed_count" not in channel.calls[0]["value"]


# ---------------------------------------------------------------------------
# 4. Boot source on disk loss / fresh start
# ---------------------------------------------------------------------------


async def test_disk_lost_fresh_start_boots_from_main_in_vm_only(monkeypatch):
    def run_fresh_start(policy_name: str):
        _set_policy(monkeypatch, policy_name)
        described = []

        def describe(project, grant, target_ref=None):
            described.append(target_ref)
            if target_ref is None:
                return _base(MAIN, "2" * 40)
            return _base(target_ref, "1" * 40)

        transport = SimpleNamespace(describe=Mock(side_effect=describe), exchange=AsyncMock())
        lifecycle, _ = _make_lifecycle(transport=transport)
        row = _default_row(state="allocating", resource=None)

        host = SimpleNamespace(
            resource={},
            git_handler=None,
            create=AsyncMock(),
            _open_control=AsyncMock(),
            control=AsyncMock(
                side_effect=lambda action, _val=None: (
                    {
                        "runtime": {
                            "runtime_protocol": 1,
                            "file_protocol": 1,
                            "capabilities": ["workspace_lifecycle", "native_files", "file_watch"],
                            "build_id": "a" * 64,
                        },
                        "workbench_lifecycle": 1,
                    }
                    if action == "capabilities"
                    else {}
                )
            ),
        )
        return lifecycle, row, host, described

    # vm-only: fresh allocation on default workspace MUST boot from main (target_ref=None),
    # ignoring any stale refs/heads/workspaces/<owner> branch.
    lifecycle_vm, row_vm, host_vm, described_vm = run_fresh_start("vm-only")
    await lifecycle_vm._start_host(row_vm, SimpleNamespace(org_id="org-1"), host_vm)
    assert described_vm == [None]
    assert host_vm.resource["initialization"]["source_ref"] == MAIN
    assert host_vm.resource["initialization"]["base_oid"] == "2" * 40

    # open: fresh allocation checks row["target_ref"] first (workspace branch).
    lifecycle_open, row_open, host_open, described_open = run_fresh_start("open")
    await lifecycle_open._start_host(row_open, SimpleNamespace(org_id="org-1"), host_open)
    assert described_open == [WORKSPACE_REF]
    assert host_open.resource["initialization"]["source_ref"] == WORKSPACE_REF
    assert host_open.resource["initialization"]["base_oid"] == "1" * 40


# ---------------------------------------------------------------------------
# 5. Node worker shared-git.mjs snapshot behavior
# ---------------------------------------------------------------------------


def test_shared_git_mjs_snapshot_linear_history_and_changed_file_count(tmp_path):
    node = shutil.which("node")
    git_bin = shutil.which("git")
    if not node or not git_bin:
        pytest.skip("node or git not installed")

    worker_dir = Path(__file__).resolve().parents[4] / "agent-worker"
    script = tmp_path / "run.mjs"
    work = tmp_path / "workspace"
    work.mkdir()

    script.write_text(
        f"""
import {{ execFile }} from 'node:child_process';
import {{ writeFile }} from 'node:fs/promises';
import {{ snapshotParents, snapshotCommitMessage }} from {json.dumps(str(worker_dir / 'shared-git.mjs'))};

const work = {json.dumps(str(work))};
const gitBin = {json.dumps(git_bin)};
const run = args => new Promise((resolve, reject) => {{
  execFile(gitBin, args, {{
    cwd: work,
    env: {{
      PATH: process.env.PATH,
      GIT_AUTHOR_NAME: 'Backup',
      GIT_AUTHOR_EMAIL: 'backup@example.test',
      GIT_COMMITTER_NAME: 'Backup',
      GIT_COMMITTER_EMAIL: 'backup@example.test',
    }},
    encoding: 'utf8',
  }}, (err, stdout, stderr) => {{
    if (err) {{ err.output = stdout; err.stderr = stderr; reject(err); }}
    else resolve(stdout.trimEnd());
  }});
}});

await run(['init', '--initial-branch=workspace']);
await writeFile(`${{work}}/a.txt`, 'v1\\n');
await run(['add', '-A']);
const tree1 = await run(['write-tree']);
const parents1 = await snapshotParents(run, null, null);
const msg1 = await snapshotCommitMessage(run, {{
  message: 'Workspace backup',
  parents: parents1,
  tree: tree1,
  includeChangedCount: true,
}});
const c1 = await run(['commit-tree', tree1, '-m', msg1]);
// Advance local HEAD to c1, then simulate a subsequent backup c2 where main moved ahead of HEAD
await run(['update-ref', 'refs/heads/workspace', c1]);
await writeFile(`${{work}}/a.txt`, 'v2\\n');
await writeFile(`${{work}}/b.txt`, 'new\\n');
await run(['add', '-A']);
const tree2 = await run(['write-tree']);
const parents2 = await snapshotParents(run, c1, c1);
const msg2 = await snapshotCommitMessage(run, {{
  message: 'Workspace backup',
  parents: parents2,
  tree: tree2,
  includeChangedCount: true,
}});
const c2 = await run(['commit-tree', tree2, '-p', parents2[0], '-m', msg2]);

// Now remote tip is c2 while local HEAD is still c1 (ancestor of c2) -> linear single parent [c2]!
const parents3 = await snapshotParents(run, c2, c1);
console.log(JSON.stringify({{ parents1, msg1, parents2, msg2, parents3, c1, c2 }}));
""",
        encoding="utf-8",
    )
    proc = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip())
    assert out["parents1"] == []
    assert out["msg1"] == "Workspace backup (1 file)"
    assert out["parents2"] == [out["c1"]]
    assert out["msg2"] == "Workspace backup (2 files)"
    assert out["parents3"] == [out["c2"]]
