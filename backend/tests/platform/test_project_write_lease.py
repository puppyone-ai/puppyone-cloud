from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from src.exceptions import AppException
from src.infra.s3.service import S3Service
from src.platform.project.write_lease import (
    PHYSICAL_S3_WRITE_LEASE_TTL_SECONDS,
    ProjectWriteLease,
    active_project_write_lease,
    git_project_write_lease,
)


class LeaseRepositoryStub:
    def __init__(self, *, renew: bool = True) -> None:
        self.renew_result = renew
        self.acquired: list[dict] = []
        self.renewed: list[dict] = []
        self.released: list[dict] = []

    def acquire(self, **kwargs):
        self.acquired.append(kwargs)
        return {"outcome": "acquired"}

    def renew(self, **kwargs):
        self.renewed.append(kwargs)
        return self.renew_result

    def release(self, **kwargs):
        self.released.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_nested_logical_lease_reuses_only_a_live_shared_claim() -> None:
    repository = LeaseRepositoryStub()
    outer = ProjectWriteLease("project-1", "outer", repository=repository)

    async with outer:
        assert active_project_write_lease("project-1") is outer
        async with ProjectWriteLease(
            "project-1", "nested", repository=repository
        ):
            assert len(repository.acquired) == 1

    assert active_project_write_lease("project-1") is None
    assert len(repository.released) == 1


@pytest.mark.asyncio
async def test_copied_background_context_cannot_reuse_released_outer_claim(
    monkeypatch,
) -> None:
    repository = LeaseRepositoryStub()
    outer = ProjectWriteLease(
        "project-1",
        "initializer",
        repository=repository,
        initialization_operation_key="operation-1",
        initialization_actor="actor-1",
    )
    continue_background = asyncio.Event()
    recorded: list[dict] = []

    class RecordingChildLease:
        def __init__(self, project_id, operation, **kwargs):
            recorded.append(
                {"project_id": project_id, "operation": operation, **kwargs}
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        "src.platform.project.write_lease.ProjectWriteLease",
        RecordingChildLease,
    )
    s3 = object.__new__(S3Service)

    async def late_write() -> None:
        await continue_background.wait()
        async with s3._project_write_guard(
            "version/project-1/objects/aa/blob", "put_object"
        ):
            pass

    async with outer:
        background = asyncio.create_task(late_write())
    continue_background.set()
    await background

    assert recorded == [
        {
            "project_id": "project-1",
            "operation": "s3.put_object",
            "ttl_seconds": PHYSICAL_S3_WRITE_LEASE_TTL_SECONDS,
            "reuse_active": False,
        }
    ]


@pytest.mark.asyncio
async def test_physical_child_inherits_live_initializer_proof(monkeypatch) -> None:
    repository = LeaseRepositoryStub()
    recorded: list[dict] = []

    class RecordingChildLease:
        def __init__(self, project_id, operation, **kwargs):
            recorded.append(
                {"project_id": project_id, "operation": operation, **kwargs}
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    s3 = object.__new__(S3Service)
    outer = ProjectWriteLease(
        "project-1",
        "initializer",
        repository=repository,
        initialization_operation_key="operation-1",
        initialization_actor="actor-1",
        initialization_worker="worker-1",
    )
    async with outer:
        monkeypatch.setattr(
            "src.platform.project.write_lease.ProjectWriteLease",
            RecordingChildLease,
        )
        async with s3._project_write_guard(
            "projects/project-1/uploads/staged", "put_object"
        ):
            pass

    assert recorded[0]["reuse_active"] is False
    assert recorded[0]["ttl_seconds"] == PHYSICAL_S3_WRITE_LEASE_TTL_SECONDS
    assert recorded[0]["initialization_operation_key"] == "operation-1"
    assert recorded[0]["initialization_actor"] == "actor-1"
    assert recorded[0]["initialization_worker"] == "worker-1"


@pytest.mark.asyncio
async def test_renewal_loss_waits_for_real_thread_before_releasing_lease() -> None:
    repository = LeaseRepositoryStub(renew=False)
    started = threading.Event()
    finish = threading.Event()
    s3 = object.__new__(S3Service)

    def long_physical_write() -> str:
        started.set()
        finish.wait(timeout=5)
        return "done"

    async def write() -> None:
        async with ProjectWriteLease(
            "project-1",
            "s3.put_object",
            repository=repository,
            ttl_seconds=30,
            heartbeat_interval_seconds=0.01,
            reuse_active=False,
        ):
            await s3._run_sync(long_physical_write)

    task = asyncio.create_task(write())
    await asyncio.to_thread(started.wait, 1)
    for _ in range(100):
        if repository.renewed:
            break
        await asyncio.sleep(0.005)

    assert repository.renewed
    assert not task.done()
    assert repository.released == []

    finish.set()
    with pytest.raises(AppException) as caught:
        await task
    assert caught.value.details["code"] == "project_write_lease_lost"
    assert len(repository.released) == 1


def _git_request(
    path: str,
    *,
    method: str,
    query: dict[str, str] | None = None,
    path_params: dict[str, str] | None = None,
):
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        method=method,
        query_params=query or {},
        path_params=path_params or {"project_id": "project-1"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "method", "query"),
    [
        (
            "/git/project-1.git/info/refs",
            "GET",
            {"service": "git-upload-pack"},
        ),
        ("/git/project-1.git/git-upload-pack", "POST", {}),
        ("/git/project-1.git/health", "GET", {}),
    ],
)
async def test_git_read_requests_hold_project_lifecycle_lease(
    monkeypatch,
    path: str,
    method: str,
    query: dict[str, str],
) -> None:
    events: list[tuple[str, str, str]] = []

    async def resolve(_project_id, _request):
        return {"_runtime_grant": SimpleNamespace(can_write=False)}

    class Lease:
        def __init__(self, project_id: str, operation: str):
            self.project_id = project_id
            self.operation = operation

        async def __aenter__(self):
            events.append(("enter", self.project_id, self.operation))
            return self

        async def __aexit__(self, *_args):
            events.append(("exit", self.project_id, self.operation))
            return False

    monkeypatch.setattr(
        "src.version_engine.entrypoints.git.auth.resolve_git_project_auth",
        resolve,
    )
    monkeypatch.setattr("src.platform.project.write_lease.ProjectWriteLease", Lease)

    dependency = git_project_write_lease(
        _git_request(path, method=method, query=query)
    )
    await anext(dependency)
    assert events == [("enter", "project-1", f"git.{method.lower()}")]

    await dependency.aclose()
    assert events[-1] == ("exit", "project-1", f"git.{method.lower()}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "method", "query"),
    [
        ("/git/project-1.git/git-receive-pack", "POST", {}),
        ("/git/project-1.git/rebuild-cache", "POST", {}),
        (
            "/git/project-1.git/info/refs",
            "GET",
            {"service": "git-receive-pack"},
        ),
    ],
)
async def test_git_mutations_still_reject_read_only_runtime_grants(
    monkeypatch,
    path: str,
    method: str,
    query: dict[str, str],
) -> None:
    lease_entered = False

    async def resolve(_project_id, _request):
        return {"_runtime_grant": SimpleNamespace(can_write=False)}

    class Lease:
        def __init__(self, _project_id: str, _operation: str):
            pass

        async def __aenter__(self):
            nonlocal lease_entered
            lease_entered = True
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        "src.version_engine.entrypoints.git.auth.resolve_git_project_auth",
        resolve,
    )
    monkeypatch.setattr("src.platform.project.write_lease.ProjectWriteLease", Lease)

    dependency = git_project_write_lease(
        _git_request(path, method=method, query=query)
    )
    with pytest.raises(AppException) as caught:
        await anext(dependency)

    assert caught.value.status_code == 403
    assert lease_entered is False


@pytest.mark.asyncio
async def test_access_key_git_read_is_resolved_then_leased(monkeypatch) -> None:
    events: list[tuple[str, str]] = []

    async def resolve(access_key, _request):
        assert access_key == "access-1"
        return "project-1", {
            "_runtime_grant": SimpleNamespace(can_write=False),
        }

    class Lease:
        def __init__(self, project_id: str, operation: str):
            events.append((project_id, operation))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        "src.version_engine.entrypoints.git.router.resolve_git_access_point",
        resolve,
    )
    monkeypatch.setattr("src.platform.project.write_lease.ProjectWriteLease", Lease)

    dependency = git_project_write_lease(
        _git_request(
            "/git/ap/access-1.git/git-upload-pack",
            method="POST",
            path_params={"access_key": "access-1"},
        )
    )
    await anext(dependency)
    await dependency.aclose()

    assert events == [("project-1", "git.post")]
