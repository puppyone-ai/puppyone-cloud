from pathlib import Path

CHANGE = (
    Path(__file__).resolve().parents[3] / "openspec" / "changes" / "add-database-release-governance"
)
ORCHESTRATION_CHANGE = (
    Path(__file__).resolve().parents[3]
    / "openspec"
    / "changes"
    / "update-database-release-orchestration"
)


def test_database_release_governance_change_has_strict_shape() -> None:
    for name in ("proposal.md", "design.md", "tasks.md"):
        assert (CHANGE / name).is_file()

    proposal = (CHANGE / "proposal.md").read_text()
    assert "## Why" in proposal
    assert "## What Changes" in proposal
    assert "## Impact" in proposal

    spec = (CHANGE / "specs/database-release-governance/spec.md").read_text()
    assert "## ADDED Requirements" in spec
    assert spec.count("### Requirement:") >= 6
    assert spec.count("#### Scenario:") >= spec.count("### Requirement:")
    assert "supabase/data_migrations" in spec
    assert "self-hosted" in spec
    assert "Contract" in spec


def test_database_release_orchestration_change_has_strict_shape() -> None:
    for name in ("proposal.md", "design.md", "tasks.md"):
        assert (ORCHESTRATION_CHANGE / name).is_file()

    proposal = (ORCHESTRATION_CHANGE / "proposal.md").read_text()
    assert "## Why" in proposal
    assert "## What Changes" in proposal
    assert "## Impact" in proposal

    spec = (ORCHESTRATION_CHANGE / "specs" / "database-release-governance" / "spec.md").read_text()
    assert "## MODIFIED Requirements" in spec
    assert "### Requirement: Protected environment promotion" in spec
    assert spec.count("#### Scenario:") >= 4
