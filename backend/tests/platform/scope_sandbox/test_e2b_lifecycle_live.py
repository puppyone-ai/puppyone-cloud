"""Opt-in E2B acceptance using only synthetic files and the default template.

Set CLOUD_AGENT_TEST_E2B_PROVIDER=1 and E2B_API_KEY explicitly. No application
artifact, customer repository, database, or deployment is used. This verifies
provider capabilities, not the PiWorker/controller integration contract.
"""

import asyncio
import json
import os
import shlex
from contextlib import contextmanager
from time import monotonic
from uuid import uuid4

import pytest
from e2b import AsyncSandbox

pytestmark = [
    pytest.mark.integration,
    pytest.mark.network,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("CLOUD_AGENT_TEST_E2B_PROVIDER") != "1",
        reason="Explicit opt-in for synthetic E2B provider acceptance required",
    ),
]

_MANIFEST = """import hashlib,json,os,pathlib,subprocess
root=pathlib.Path('/workspace/repo')
files={}
for path in sorted(root.rglob('*')):
    relative=path.relative_to(root)
    if '.git' in relative.parts: continue
    if path.is_symlink(): files[str(relative)]={'link':os.readlink(path)}
    elif path.is_file():
        files[str(relative)]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                              'mode':path.stat().st_mode & 511}
def git(*args):
    return subprocess.check_output(['git','-C',str(root),*args]).decode()
print(json.dumps({'files':files,'head':git('rev-parse','HEAD'),
                  'history':git('log','--format=%H'),
                  'status':git('status','--porcelain=v1'),
                  'staged':git('diff','--cached'),'unstaged':git('diff')},sort_keys=True))
"""


async def _run(sandbox, command, **kwargs):
    result = await sandbox.commands.run(command, timeout=30, **kwargs)
    assert result.exit_code == 0, result.stderr
    return result.stdout.strip()


async def _manifest(sandbox):
    return json.loads(await _run(sandbox, "python3 -c " + shlex.quote(_MANIFEST)))


async def test_native_pause_and_snapshot_preserve_synthetic_git_worktree(record_property):
    api_key = os.environ["E2B_API_KEY"]
    run_id = str(uuid4())
    sandboxes, snapshots = [], []

    @contextmanager
    def timed(name):
        start = monotonic()
        try:
            yield
        finally:
            record_property(name + "_seconds", round(monotonic() - start, 3))

    async def create(template=None):
        sandbox = await AsyncSandbox.create(
            template=template,
            api_key=api_key,
            timeout=300,
            allow_internet_access=False,
            metadata={"purpose": "synthetic-recovery-test", "test_run": run_id},
            request_timeout=60,
        )
        sandboxes.append(sandbox.sandbox_id)
        record_property("sandbox_id", sandbox.sandbox_id)
        return sandbox

    try:
        with timed("create"):
            source = await create()
        with timed("prepare_fake_files"):
            await _run(
                source,
                "mkdir -p /workspace/repo && chown user:user /workspace/repo",
                user="root",
            )
            await _run(
                source,
                "git init --bare --initial-branch=main /tmp/fake-remote.git && "
                "git init --initial-branch=main /workspace/repo && "
                'git -C /workspace/repo config user.name "Sandbox Test" && '
                "git -C /workspace/repo config user.email test@example.invalid && "
                "mkdir -p /workspace/repo/Archive /workspace/repo/研究资料",
            )
            files = {
                "Herb.md": "# Synthetic notes\nInitial content\n",
                "Archive/old.md": "# Archived fake document\n",
                "研究资料/例子.md": "# 假文件\n仅用于测试\n",
                ".hidden": "synthetic hidden file\n",
                "run.sh": "#!/bin/sh\necho synthetic\n",
                "sample.bin": bytes(range(256)) * 16,
            }
            for path, content in files.items():
                await source.files.write("/workspace/repo/" + path, content)
            await _run(
                source,
                "cd /workspace/repo && chmod 755 run.sh && ln -s Herb.md note-link && "
                'git add . && git commit -m "Synthetic initial files" && '
                "git remote add origin /tmp/fake-remote.git && git push -u origin main",
            )
            await source.files.write("/workspace/repo/Herb.md", "staged edit\n")
            await _run(source, "git -C /workspace/repo add Herb.md")
            await source.files.write("/workspace/repo/Herb.md", "staged and unstaged edit\n")
            await source.files.write("/workspace/repo/untracked.md", "synthetic draft\n")
            await source.files.write("/tmp/fake-scratch.txt", "synthetic scratch\n")
            baseline = await _manifest(source)
            assert "MM Herb.md" in baseline["status"]
            assert "?? untracked.md" in baseline["status"]
            assert baseline["files"]["run.sh"]["mode"] == 0o755
            assert baseline["files"]["note-link"]["link"] == "Herb.md"

        for turn in range(3):
            with timed(f"pause_{turn}"):
                assert await source.pause(request_timeout=60)
                assert not await source.is_running(request_timeout=30)
            with timed(f"wake_{turn}"):
                identity = source.sandbox_id
                source = await AsyncSandbox.connect(
                    identity, api_key=api_key, timeout=300, request_timeout=60
                )
                assert source.sandbox_id == identity
            # No upload, clone, or pull on wake: the original worktree must persist.
            assert await _manifest(source) == baseline

        with timed("snapshot"):
            await _run(source, "sync")
            snapshot = await source.create_snapshot(request_timeout=90)
            snapshots.append(snapshot.snapshot_id)
            record_property("snapshot_id", snapshot.snapshot_id)
        await source.files.write("/workspace/repo/Herb.md", "changed after snapshot\n")
        await source.files.write("/workspace/repo/after-snapshot.md", "not in checkpoint\n")
        assert await _manifest(source) != baseline
        assert await source.kill(request_timeout=60)
        with timed("restore_after_source_deletion"):
            restored = await create(snapshot.snapshot_id)
            assert restored.sandbox_id != source.sandbox_id
        assert await _manifest(restored) == baseline
        # Whole-VM snapshots also contain /tmp; scratch is not a separate authority.
        assert await restored.files.read("/tmp/fake-scratch.txt") == "synthetic scratch\n"
        with timed("commit_push_to_fake_remote"):
            await _run(
                restored,
                'cd /workspace/repo && git add . && git commit -m "Recovered edits" && '
                "git push origin main",
            )
            head = await _run(restored, "git -C /workspace/repo rev-parse HEAD")
            remote = await _run(
                restored, "git --git-dir=/tmp/fake-remote.git rev-parse refs/heads/main"
            )
            assert head == remote
            assert not await _run(restored, "git -C /workspace/repo status --porcelain")
            assert await _run(restored, "git -C /workspace/repo rev-list --count HEAD") == "2"
    finally:
        # Try every owned resource even when one cleanup call fails. Never list or
        # remove other sandboxes/templates from the provider account.
        results = list(
            await asyncio.gather(
                *(
                    AsyncSandbox.kill(identity, api_key=api_key, request_timeout=60)
                    for identity in sandboxes
                ),
                return_exceptions=True,
            )
        )
        # A snapshot is a template dependency of restored sandboxes. Deleting it
        # concurrently with those sandboxes can fail while they are still alive.
        results.extend(
            await asyncio.gather(
                *(
                    AsyncSandbox.delete_snapshot(identity, api_key=api_key, request_timeout=60)
                    for identity in snapshots
                ),
                return_exceptions=True,
            )
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        assert not failures, f"Synthetic E2B resource cleanup failed: {failures!r}"
