"""Cancellation must preserve ownership; limits must stop real subprocesses."""

import asyncio
import io
import os
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError

from src.config import Settings
from src.platform.access.adapters.git import execution
from src.platform.access.adapters.git.execution import admission, run_git, run_owned

pytestmark = pytest.mark.hosting_component


@pytest.mark.asyncio
@pytest.mark.parametrize("limit_source", ["plan", "deployment", "upload_configuration"])
async def test_native_request_limits_reject_before_spooling_or_running_git(
    monkeypatch, limit_source
):
    from src.platform.access.adapters.git import router

    calls = []

    def effective_receive_cap(project_id):
        assert project_id == "limited-project"
        calls.append(project_id)
        return 4 if limit_source == "plan" else 1024**3

    monkeypatch.setattr(router, "_git_receive_max_body_bytes", effective_receive_cap)
    monkeypatch.setattr(router.settings, "GIT_MAX_UPLOAD_PACK_BYTES", 4)
    declared = (5 if limit_source == "upload_configuration" else
                (4 if limit_source == "plan" else 1024**3) + router.MAX_RECEIVE_COMMAND_BYTES + 1)
    request = Request({"type": "http", "headers": [(b"content-length", str(declared).encode())]})
    # No receive callable and no Git endpoint: rejection must precede both
    # reading the request stream and attempting transport materialization.
    with pytest.raises(HTTPException) as caught:
        await router._native_rpc(
            None,
            request,
            upload=limit_source == "upload_configuration",
            project_id="limited-project",
        )
    assert caught.value.status_code == 413
    assert calls == ([] if limit_source == "upload_configuration" else ["limited-project"])


@pytest.mark.parametrize("cancelled", [False, True], ids=["deadline", "disconnect"])
def test_snapshot_progress_stops_expensive_reads_after_owned_execution_ends(cancelled):
    from src.infra.execution.context import Execution, current_execution
    from src.version_engine.read.repository_snapshot import RepositorySnapshot

    # Health and graph walks use this same snapshot progress callback. They
    # must stop even when they never invoke a Git child or publish a ref.
    snapshot = object.__new__(RepositorySnapshot)
    snapshot._closed = False
    snapshot._next_renewal = float("inf")
    execution = Execution(deadline=time.monotonic() - 1 if not cancelled else float("inf"))
    if cancelled:
        execution.cancelled.set()
    token = current_execution.set(execution)
    try:
        with pytest.raises(RuntimeError if cancelled else TimeoutError):
            snapshot.check_live()
    finally:
        current_execution.reset(token)
    snapshot.check_live()  # unrelated non-owned readers retain their contract


@pytest.mark.asyncio
async def test_disconnected_caller_stops_owned_work_before_releasing_its_scope():
    from src.infra.execution.context import checkpoint

    exited = threading.Event()

    def worker():
        try:
            while True:
                checkpoint()
                time.sleep(0.005)
        finally:
            exited.set()

    async def disconnected():
        return True

    with pytest.raises(RuntimeError, match="cancelled"):
        await run_owned(worker, cancel_when=disconnected)
    assert exited.is_set()


@pytest.mark.asyncio
async def test_cancelled_native_worker_is_joined_before_lease_scope_exits():
    started, finish, exited = threading.Event(), threading.Event(), threading.Event()

    def worker():
        started.set()
        assert finish.wait(3)
        exited.set()

    task = asyncio.create_task(run_owned(worker))
    while not started.is_set():
        await asyncio.sleep(0.001)
    task.cancel()
    await asyncio.sleep(0.03)
    assert not task.done() and not exited.is_set()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exited.is_set()


@pytest.mark.parametrize("capacity", [1, 4])
def test_native_worker_capacity_has_no_unbounded_wait_queue(monkeypatch, capacity):
    monkeypatch.setattr(execution, "_slots", threading.BoundedSemaphore(capacity))
    with ExitStack() as active:
        for _ in range(capacity):
            active.enter_context(admission())
        with pytest.raises(HTTPException) as caught, admission():
            pytest.fail("worker beyond the configured capacity must not be admitted")
        assert caught.value.status_code == 503
        assert caught.value.headers == {"Retry-After": "1"}
    with ExitStack() as active:
        for _ in range(capacity):
            active.enter_context(admission())


def test_native_worker_limit_is_loaded_from_environment_at_startup(tmp_path):
    # A fresh process proves the real gate uses the environment setting without
    # reloading globals or changing the admission gate used by other tests.
    script = """
from contextlib import ExitStack
from fastapi import HTTPException
from src.platform.access.adapters.git.execution import MAX_CONCURRENT_OPERATIONS, admission
assert MAX_CONCURRENT_OPERATIONS == 4
with ExitStack() as active:
    for _ in range(4):
        active.enter_context(admission())
    try:
        with admission():
            raise AssertionError('fifth worker admitted')
    except HTTPException as exc:
        assert exc.status_code == 503
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=dict(
            os.environ,
            PYTHONPATH=str(Path(__file__).resolve().parents[3]),
            GIT_MAX_CONCURRENT_OPERATIONS="4",
        ),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "unlimited"])
def test_native_worker_configuration_rejects_invalid_limits(monkeypatch, value):
    monkeypatch.setenv("GIT_MAX_CONCURRENT_OPERATIONS", value)
    with pytest.raises(ValidationError, match="GIT_MAX_CONCURRENT_OPERATIONS"):
        Settings(_env_file=None, APP_ENV="test")


def test_native_worker_slot_is_released_on_error_and_retained_until_stream_close(monkeypatch):
    monkeypatch.setattr(execution, "_slots", threading.BoundedSemaphore(1))
    with pytest.raises(RuntimeError, match="failed"), admission():
        raise RuntimeError("failed")

    response = execution.OwnedGitResponse(io.BytesIO(b"pack"))
    try:
        with admission() as retain:
            retain(response)
        with pytest.raises(HTTPException) as caught, admission():
            pytest.fail("stream must still own its slot")
        assert caught.value.status_code == 503
    finally:
        response.close()
    response.close()  # idempotent close must not over-release the bounded gate
    with admission():
        pass


def test_timed_out_native_subprocess_and_child_are_reaped(tmp_path):
    pid = tmp_path / "child"
    script = f"import subprocess,time; p=subprocess.Popen(['sleep','20']); open({str(pid)!r},'w').write(str(p.pid)); time.sleep(20)"
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        run_git([sys.executable, "-c", script], env=dict(os.environ), directory=tmp_path, timeout=1)
    assert time.monotonic() - start < 5
    child = int(pid.read_text())
    # A reparented zombie may remain until the container's init reaps it; it is
    # no longer executing and cannot hold a file or issue remote I/O.
    if sys.platform == "linux":
        try:
            status = Path(f"/proc/{child}/status").read_text()
        except FileNotFoundError:
            state = ""
        else:
            state = next(
                (line.split()[1] for line in status.splitlines() if line.startswith("State:")), ""
            )
    else:
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(child)], capture_output=True, text=True
        ).stdout.strip()
    assert not state or state.startswith("Z")


@pytest.mark.skipif(
    sys.platform != "linux", reason="Linux production rlimit acceptance runs in Docker"
)
def test_native_child_memory_and_file_limits_fail_without_host_exhaustion(tmp_path):
    memory = run_git(
        [sys.executable, "-c", "bytes(900 * 1024**2)"],
        env=dict(os.environ),
        directory=tmp_path,
        timeout=5,
    )
    assert memory.returncode != 0 and b"MemoryError" in memory.stderr
    path = tmp_path / "oversize"
    code = f"with open({str(path)!r}, 'wb') as f: f.truncate(385 * 1024**2)"
    disk = run_git(
        [sys.executable, "-c", code], env=dict(os.environ), directory=tmp_path, timeout=5
    )
    assert disk.returncode != 0 and path.stat().st_size <= 384 * 1024**2
