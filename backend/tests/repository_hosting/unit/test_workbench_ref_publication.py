"""Explicit workbench refs use native CAS without owning repository HEAD."""
import base64
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.platform.access.adapters.agent.runtime.control.git_transport import RunGitTransport, RunPublication
from src.version_engine.read.ref_metadata import GitBranchBase
from src.version_engine.write_engine.ref_transaction import RefEdit, RefState


def metadata(fmt="sha1", *, head=b"refs/heads/main", target=b"refs/heads/workbench", present=True):
    width = 40 if fmt == "sha1" else 64
    def row(name, state):
        return {"name_b64": base64.b64encode(name).decode(), "state": state.wire(fmt)}
    refs = [row(b"HEAD", RefState(target=head)), row(b"refs/heads/main", RefState(oid="a" * width))]
    if present:
        refs.append(row(target, RefState(oid="b" * width)))
    return {"object_format": fmt, "generation": 7, "refs": refs}


@pytest.mark.parametrize("fmt,width", [("sha1", 40), ("sha256", 64)])
@pytest.mark.parametrize("present", [False, True])
def test_explicit_ref_does_not_guard_or_edit_display_head(fmt, width, present):
    base = GitBranchBase.from_metadata(metadata(fmt, present=present), target_ref="refs/heads/workbench")
    assert base.ref_name == b"refs/heads/workbench"
    assert base.head_guard is None
    assert base.expected.oid == ("b" * width if present else None)
    assert GitBranchBase.parse(base.wire()) == base
    assert base.edits("c" * width) == (RefEdit(base.ref_name, base.expected, RefState(oid="c" * width)),)
    changed_display = GitBranchBase.from_metadata(
        metadata(fmt, head=b"refs/heads/another", present=present), target_ref="refs/heads/workbench")
    assert changed_display == base


def test_explicit_ref_does_not_require_symbolic_display_head():
    value = metadata()
    value["refs"][0]["state"] = RefState(oid="a" * 40).wire("sha1")
    assert GitBranchBase.from_metadata(value, target_ref="refs/heads/workbench").head_guard is None
    with pytest.raises(ValueError, match="selected branch"):
        GitBranchBase.from_metadata(value)


@pytest.mark.parametrize("ref", ["HEAD", "refs/tags/x", "refs/heads/../x", "refs/heads/x.lock", "refs/heads/x\n"])
def test_explicit_ref_requires_valid_native_branch(ref):
    with pytest.raises(ValueError):
        GitBranchBase.from_metadata(metadata(), target_ref=ref)


def test_wire_cannot_disagree_about_target():
    wire = GitBranchBase.from_metadata(metadata()).wire()
    with pytest.raises(ValueError, match="target"):
        GitBranchBase.parse({**wire, "target_ref": "refs/heads/other"})


def test_explicit_candidate_remains_exact_and_generation_bound():
    base = GitBranchBase.from_metadata(metadata(), target_ref="refs/heads/workbench")
    candidate = "c" * 40
    publication = RunPublication("original-run", base, candidate)
    publication.validate(base.edits(candidate))
    for edits in [base.edits("d" * 40),
                  (RefEdit(b"refs/heads/main", base.expected, RefState(oid=candidate)),),
                  (*base.edits(candidate), RefEdit(b"HEAD", RefState(target=base.ref_name)))]:
        with pytest.raises(PermissionError, match="admitted run candidate"):
            publication.validate(edits)
    assert base.generation == 7


def test_transport_describes_explicit_ref_using_one_metadata_read():
    manager = SimpleNamespace(get_native_ref_metadata=Mock(return_value=metadata()))
    wire = RunGitTransport(manager).describe("project", "grant", target_ref="refs/heads/workbench")
    assert wire["target_ref"] == "refs/heads/workbench" and wire["head_guard"] is None
    manager.get_native_ref_metadata.assert_called_once_with("project", "grant")
