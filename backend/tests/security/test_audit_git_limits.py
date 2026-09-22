"""ISSUE-014 — git transport input-size limits & subprocess timeout.

Matches v2-audit's implementation: `_spool_git_request_body` enforces a
compressed-body cap (Content-Length precheck + streaming guard) and rejects
with HTTP 400; `run_git` bounds subprocess wall-clock time. Hermetic: a fake
Request (no ASGI server, no Supabase); run_git shells out to local `git` only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException


class _FakeStreamRequest:
    """Minimal stand-in for starlette Request: .headers + async .stream()."""

    def __init__(self, chunks: list[bytes], headers: dict | None = None):
        self._chunks = chunks
        self.headers = headers or {}

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


def _router():
    from src.version_engine.entrypoints.git import router
    return router


# ── _spool_git_request_body cap ─────────────────────────────────────────────

def test_spool_rejects_streamed_body_over_cap_and_cleans_up(tmp_path, monkeypatch):
    router = _router()
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))

    req = _FakeStreamRequest([b"a" * 600, b"b" * 600])  # 1200 bytes, no content-length

    with pytest.raises(HTTPException) as ei:
        asyncio.run(router._spool_git_request_body(req, max_body_bytes=1000))
    assert ei.value.status_code == 400
    # No partial spool file left behind.
    assert list(tmp_path.glob("puppyone-git-rpc-*")) == []


def test_spool_rejects_declared_content_length_over_cap(tmp_path):
    router = _router()
    req = _FakeStreamRequest([b"x"], headers={"content-length": "99999"})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(router._spool_git_request_body(req, max_body_bytes=1000))
    assert ei.value.status_code == 400


def test_spool_accepts_body_within_cap(tmp_path):
    router = _router()
    req = _FakeStreamRequest([b"hello ", b"world"])
    path = asyncio.run(router._spool_git_request_body(req, max_body_bytes=1000))
    try:
        assert Path(path).read_bytes() == b"hello world"
    finally:
        router._unlink_temp(Path(path))


def test_spool_helper_can_be_unbounded_for_non_git_callers(tmp_path):
    router = _router()
    req = _FakeStreamRequest([b"x" * 5000])
    path = asyncio.run(router._spool_git_request_body(req, max_body_bytes=None))
    try:
        assert Path(path).stat().st_size == 5000
    finally:
        router._unlink_temp(Path(path))


@pytest.mark.parametrize(
    ("entitlement", "hard_cap", "expected"),
    [
        (None, 1000, 1000),
        (0, 1000, 1000),
        (-1, 1000, 1000),
        (400, 1000, 400),
        (2000, 1000, 1000),
    ],
)
def test_receive_pack_always_has_smaller_finite_cap(
    monkeypatch, entitlement, hard_cap, expected
):
    router = _router()
    monkeypatch.setattr(router.settings, "GIT_MAX_RECEIVE_PACK_BYTES", hard_cap)
    assert router._effective_receive_pack_cap(entitlement) == expected


# ── run_git timeout parameter ───────────────────────────────────────────────

def test_run_git_happy_path_with_timeout():
    """The timeout kwarg must not break a normal fast git invocation."""
    from src.version_engine.adapters.git.protocol import run_git

    out = run_git(["--version"], timeout=30)
    assert b"git version" in out


def test_run_git_times_out(monkeypatch):
    """A hanging git process surfaces as RuntimeError, not a hang."""
    import subprocess

    from src.version_engine.adapters.git import protocol

    def _boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(protocol.subprocess, "run", _boom)
    with pytest.raises(RuntimeError, match="timed out"):
        protocol.run_git(["gc"], timeout=1)
