"""What a push may contain, as a contract Desktop pins byte for byte.

Desktop used to copy server rules into its own checks and recognize refusals
by English wording. When the native Version Engine started accepting merge
commits (2026-10-07) and reworded its size refusals, Desktop kept refusing
merge tips and stopped recognizing "too large", while both repositories'
tests stayed green. This contract is the one place those facts live: change
it here and in Desktop's copy together, and the pinned digests fail until both
sides adapt (puppy-issues ISSUE-127).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.platform.access.adapters.git.native_repository import NativeGitRepository
from src.shared_types.git_pack_limits import (
    GIT_LIMIT_CODES,
    MAX_RECEIVE_PACK_BYTES,
    GitLimitExceeded,
    refusal_reason,
)
from src.version_engine.storage import git_process
from src.version_engine.storage.git_pack import GitPack

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "contracts" / "cloud-git-acceptance-v1.json"
CONTRACT_SHA256 = "49f66fd7df22c8a7d266afb2d27a87296b638f232b4cb44581a5aae9fe02cf54"
RECEIVE_PATH_SOURCES = [
    "src/version_engine/storage/git_pack.py",
    "src/version_engine/storage/git_process.py",
    "src/platform/access/adapters/git/execution.py",
    "src/platform/access/adapters/git/object_reader.py",
]


def contract():
    return json.loads(CONTRACT_PATH.read_bytes())


def test_the_contract_is_the_pinned_cross_repository_fixture():
    payload = CONTRACT_PATH.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(payload).hexdigest() == CONTRACT_SHA256
    assert contract()["contract"] == "puppyone.cloud-git-acceptance"
    assert contract()["version"] == 1


def test_any_commit_graph_is_accepted_and_lfs_is_not():
    assert contract()["commit_graph"] == {
        "merge_commits": "accepted",
        "octopus_merges": "accepted",
        "unrelated_histories": "accepted",
        "server_rewrites_history": False,
    }
    assert contract()["content"]["git_lfs"] == "unsupported"


def test_the_receive_limit_is_the_servers():
    assert contract()["limits"]["receive_pack_bytes"] == MAX_RECEIVE_PACK_BYTES


def test_the_refusal_codes_are_exactly_the_servers():
    assert contract()["refusals"]["codes"] == sorted(GIT_LIMIT_CODES)


def test_every_budget_refusal_on_the_receive_path_carries_a_registered_code():
    raised = set()
    for relative in RECEIVE_PATH_SOURCES:
        source = (ROOT / "backend" / relative).read_text()
        # A plain ValueError for a budget would reach Desktop as an unknown failure.
        assert not re.search(r"raise ValueError\([^)]*budget exceeded", source), relative
        assert not re.search(r"raise ValueError\([^)]*-byte limit for this operation", source), (
            relative
        )
        raised |= set(re.findall(r'GitLimitExceeded\(\s*"([a-z_]+)"', source))
    assert raised == GIT_LIMIT_CODES


def test_the_wire_format_is_the_contracts():
    error = GitLimitExceeded("git_object_count_limit", "Git object count budget exceeded")
    reason = refusal_reason(error)
    assert reason == "Git object count budget exceeded (puppyone-refusal=git_object_count_limit)"
    assert contract()["refusals"]["format"] == "<human reason> (puppyone-refusal=<code>)"
    assert refusal_reason(ValueError("stale ref")) == "stale ref"


def test_a_refusal_keeps_being_a_value_error_with_its_message():
    error = GitLimitExceeded("git_graph_limit", "Git graph budget exceeded")
    assert isinstance(error, ValueError) and str(error) == "Git graph budget exceeded"
    with pytest.raises(AssertionError, match="unregistered"):
        GitLimitExceeded("git_new_thing", "message")


def test_an_oversized_pack_is_refused_with_its_code():
    with GitPack("sha1") as pack, pytest.raises(GitLimitExceeded) as refused:
        pack.read(io.BytesIO(b"P" * 11), max_input_bytes=10)
    assert refused.value.code == "git_pack_too_large"
    assert str(refused.value) == "Git pack exceeds the 10-byte limit for this operation"


def test_a_scratch_disk_overrun_is_refused_with_its_code(tmp_path):
    (tmp_path / "spool").write_bytes(b"x" * 32)
    with pytest.raises(GitLimitExceeded) as refused:
        # Only the scratch budget is read; a real PackLimits cannot be this small.
        git_process.check(time.monotonic() + 60, tmp_path, limits=SimpleNamespace(scratch_bytes=1))
    assert refused.value.code == "git_processing_disk_limit"


def test_the_receive_report_carries_the_code_to_the_git_client():
    main = SimpleNamespace(name=b"refs/heads/main")
    refused = refusal_reason(GitLimitExceeded("git_graph_limit", "Git graph budget exceeded"))
    unpack = "invalid pack: " + refusal_reason(
        GitLimitExceeded(
            "git_pack_too_large", "Git pack exceeds the 1-byte limit for this operation"
        )
    )
    report = NativeGitRepository.report(
        [main], set(), {b"refs/heads/main": refused}, unpack=unpack
    ).body
    assert b"unpack invalid pack: Git pack exceeds" in report
    assert b"(puppyone-refusal=git_pack_too_large)" in report
    assert (
        b"ng refs/heads/main Git graph budget exceeded (puppyone-refusal=git_graph_limit)" in report
    )
