from __future__ import annotations

from dataclasses import dataclass

from scripts.repair_missing_project_roots import (
    RootRecoveryPlan,
    apply_project_root_recovery,
    mark_project_root_irrecoverable,
    plan_project_root_recovery,
    run,
)


@dataclass
class _History:
    entries: list[dict]

    def get_since(self, _commit: str, _scope: str | None, _limit: int):
        return list(self.entries)


class _Store:
    def __init__(self, trees: set[str]):
        self.trees = trees

    def get_object(self, object_id: str):
        if object_id not in self.trees:
            raise FileNotFoundError(object_id)
        return "tree", b""


class _Repo:
    def __init__(self, *, current: str, trees: set[str], history: list[dict]):
        self._project_id = "project-1"
        self._current = current
        self.store = _Store(trees)
        self.history = _History(history)
        self.cas_calls: list[tuple[str, str]] = []
        self.scope_cas_calls: list[tuple[str, str, str, str]] = []
        self.audits: list[tuple[str, str, dict]] = []

    def get_root_hash(self):
        return self._current

    def get_history_since(self, commit: str, scope: str | None, limit: int):
        return self.history.get_since(commit, scope, limit)

    def get_scope_state(self, _scope: str):
        return "missing", ""

    def cas_update_root_hash(self, old: str, new: str):
        self.cas_calls.append((old, new))
        return old == self._current

    def cas_update_scope(self, scope: str, old: str, new: str, head: str):
        self.scope_cas_calls.append((scope, old, new, head))
        return old == "missing"

    def record_audit(self, action: str, actor: str, detail: dict):
        self.audits.append((action, actor, detail))


def test_plan_uses_the_newest_readable_historical_root() -> None:
    repo = _Repo(
        current="missing",
        trees={"older", "newer"},
        history=[
            {"commit_id": "old", "root_hash": "older", "created_at": "2026-01-01"},
            {"commit_id": "new", "root_hash": "newer", "created_at": "2026-01-02"},
            {"commit_id": "bad", "root_hash": "missing", "created_at": "2026-01-03"},
        ],
    )

    plan = plan_project_root_recovery(repo)

    assert plan.status == "recoverable"
    assert plan.recovery_root == "newer"
    assert plan.source_commit_id == "new"


def test_plan_marks_a_currently_readable_root_healthy() -> None:
    repo = _Repo(
        current="healthy",
        trees={"healthy"},
        history=[{"commit_id": "head", "root_hash": "healthy"}],
    )
    repo.get_scope_state = lambda _scope: ("healthy", "head")

    assert plan_project_root_recovery(repo).status == "healthy"


def test_apply_uses_cas_and_audits_only_a_recoverable_plan() -> None:
    repo = _Repo(current="missing", trees={"healthy"}, history=[])
    plan = RootRecoveryPlan(
        project_id="project-1",
        current_root="missing",
        current_root_scope_hash="missing",
        recovery_root="healthy",
        source_commit_id="commit-1",
    )

    assert apply_project_root_recovery(repo, plan) is True
    assert repo.cas_calls == [("missing", "healthy")]
    assert repo.scope_cas_calls == [("", "missing", "healthy", "commit-1")]
    assert repo.audits[0][0] == "version_root_recovered_from_history"


def test_unrecoverable_plan_never_calls_cas() -> None:
    repo = _Repo(current="missing", trees=set(), history=[])

    assert apply_project_root_recovery(
        repo, RootRecoveryPlan(project_id="project-1", current_root="missing")
    ) is False
    assert repo.cas_calls == []


def test_cache_stale_plan_aligns_scope_without_rewriting_healthy_root() -> None:
    repo = _Repo(
        current="healthy",
        trees={"healthy"},
        history=[{"commit_id": "head", "root_hash": "healthy"}],
    )

    plan = plan_project_root_recovery(repo)

    assert plan.status == "cache_stale"
    assert apply_project_root_recovery(repo, plan) is True
    assert repo.cas_calls == []
    assert repo.scope_cas_calls == [("", "missing", "healthy", "head")]


def test_run_applies_a_cache_stale_plan(monkeypatch) -> None:
    repo = _Repo(
        current="healthy",
        trees={"healthy"},
        history=[{"commit_id": "head", "root_hash": "healthy"}],
    )
    repo_manager = type("Repos", (), {"get_server_repo": lambda _self, _id: repo})()
    container = type("Container", (), {"repo_manager": repo_manager})()
    monkeypatch.setattr(
        "scripts.repair_missing_project_roots.build_worker_version_engine_container",
        lambda: container,
    )

    plans, failed = run(project_ids=["project-1"], apply=True)

    assert failed == 0
    assert plans[0].status == "cache_stale"
    assert repo.scope_cas_calls == [("", "missing", "healthy", "head")]


def test_mark_irrecoverable_persists_an_incident_without_mutating_root() -> None:
    class _Query:
        def __init__(self) -> None:
            self.row: dict | None = None
            self.conflict = ""

        def upsert(self, row: dict, *, on_conflict: str):
            self.row = row
            self.conflict = on_conflict
            return self

        def execute(self):
            return object()

    query = _Query()
    client = type("Client", (), {"table": lambda _self, name: query})()
    plan = RootRecoveryPlan(
        project_id="project-1",
        current_root="a" * 40,
    )

    mark_project_root_irrecoverable(client, plan)

    assert query.conflict == "project_id"
    assert query.row is not None
    assert query.row["project_id"] == "project-1"
    assert query.row["root_hash"] == "a" * 40
    assert query.row["status"] == "irrecoverable"
    assert query.row["reason"] == (
        "current root object missing and no readable historical root tree"
    )
    assert query.row["last_detected_at"].endswith("+00:00")


def test_legacy_root_is_not_written_to_the_canonical_incident_table() -> None:
    class _Client:
        def table(self, _name):
            raise AssertionError("legacy roots must not be recorded in this table")

    plan = RootRecoveryPlan(project_id="project-1", current_root="a" * 16)

    assert mark_project_root_irrecoverable(_Client(), plan) is False
