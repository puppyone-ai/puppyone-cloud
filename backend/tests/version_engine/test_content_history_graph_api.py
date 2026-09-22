from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.version_engine.bootstrap.dependencies import (
    get_history_graph_service,
    get_product_operation_adapter,
    get_repo_manager,
    get_version_admin_service,
)
from src.version_engine.entrypoints.http.content_history import history_router
from src.version_engine.read.admin import VersionAdminService
from src.version_engine.read.history_cursor import HistoryCursorCodec
from src.version_engine.read.history_models import HistoryCursorState
from src.version_engine.read.history_graph import HistoryGraphService
from src.version_engine.storage.object_store import ObjectStore
from src.version_engine.write_engine.git_object_format import EMPTY_TREE_SHA1, encode_object
from tests.authorization_fakes import authorization_for, install_authorization


class _History:
    def __init__(
        self,
        head_commit_id: str,
        entries: list[dict],
        *,
        global_head_commit_id: str | None = None,
    ) -> None:
        self._head_commit_id = head_commit_id
        self._global_head_commit_id = global_head_commit_id or head_commit_id
        self._entries = {entry["commit_id"]: entry for entry in entries}

    def get_scope_head_commit_id(self, scope_path: str) -> str:
        assert scope_path == ""
        return self._head_commit_id

    def get_head_commit_id(self) -> str:
        return self._global_head_commit_id

    def get_root_hash(self) -> str:
        return EMPTY_TREE_SHA1

    def get_scope_state(self, scope_path: str) -> tuple[str, str]:
        assert scope_path == ""
        return EMPTY_TREE_SHA1, self._head_commit_id

    def get_latest_project_view_commit_id(self) -> str:
        return self._head_commit_id

    def get_entries(self, commit_ids: list[str]) -> list[dict]:
        return [self._entries[commit_id] for commit_id in commit_ids if commit_id in self._entries]

    def get_entry(self, commit_id: str) -> dict | None:
        return self._entries.get(commit_id)

    def get_since(self, since_commit_id: str, limit: int = 0) -> list[dict]:
        entries = list(self._entries.values())
        if since_commit_id:
            anchor = next(
                (index for index, entry in enumerate(entries) if entry["commit_id"] == since_commit_id),
                None,
            )
            return entries[anchor + 1:] if anchor is not None else []
        return entries[-limit:] if limit > 0 else entries


class _RepoManager:
    def __init__(self, store: ObjectStore, history: _History) -> None:
        self.repo = SimpleNamespace(store=store, history=history)

    def get_repo(self, project_id: str):
        assert project_id == "project-1"
        return self.repo


class _VersionRefs:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.main_commit_id = ""
        self.fail = False

    def list_project_history_refs(self, project_id: str) -> list[dict]:
        assert project_id == "project-1"
        if self.fail:
            raise RuntimeError("ref store offline")
        return [
            {
                "ref_name": "refs/heads/main",
                "ref_type": "branch",
                "commit_id": self.main_commit_id,
            },
            *self.rows,
        ]


class _Operations:
    def get_root_hash(self, project_id: str) -> str:
        assert project_id == "project-1"
        return EMPTY_TREE_SHA1


def test_topological_history_api_returns_all_refs_merge_parents_and_cursor_pages(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    base = _put_commit(store, [], 1, "Base")
    main = _put_commit(store, [base], 2, "Main work")
    feature = _put_commit(store, [base], 3, "Feature work")
    merge = _put_commit(store, [main, feature], 4, "Merge feature")
    history = _History(merge, [
        _history_entry(base, "Base", 1),
        _history_entry(main, "Main work", 2),
        # Feature intentionally has no transaction row. It exists only behind
        # the named ref and still must appear in the all-branches graph.
        _history_entry(merge, "Merge feature", 4),
    ])
    refs = _VersionRefs([
        {"ref_name": "refs/heads/feature", "ref_type": "branch", "commit_id": feature},
        {"ref_name": "refs/tags/v1", "ref_type": "tag", "commit_id": main},
    ])
    client = _client(store, history, refs)

    first_response = client.get(
        "/api/v1/content/project-1/commits",
        params={"order": "topo", "limit": 2},
    )
    assert first_response.status_code == 200
    first = first_response.json()["data"]
    assert first["head_commit_id"] == merge
    assert first["refs_included"] is True
    assert len(first["snapshot_id"]) == 64
    assert first["total"] == 4
    assert first["has_more"] is True
    assert first["next_cursor"].startswith("h1.")
    assert first["next_cursor"] != feature
    assert [commit["commit_id"] for commit in first["commits"]] == [merge, feature]
    assert first["commits"][0]["parent_ids"] == [main, feature]
    assert first["commits"][1]["message"] == "Feature work"
    assert [ref["ref_name"] for ref in first["refs"]] == [
        "refs/heads/main",
        "refs/heads/feature",
        "refs/tags/v1",
    ]

    second_response = client.get(
        "/api/v1/content/project-1/commits",
        params={"order": "topo", "limit": 2, "cursor": first["next_cursor"]},
    )
    assert second_response.status_code == 200
    second = second_response.json()["data"]
    assert [commit["commit_id"] for commit in second["commits"]] == [main, base]
    assert second["snapshot_id"] == first["snapshot_id"]
    assert second["refs_included"] is False
    assert second["refs"] == []
    assert second["has_more"] is False
    assert second["next_cursor"] is None

    ordered = [
        *[commit["commit_id"] for commit in first["commits"]],
        *[commit["commit_id"] for commit in second["commits"]],
    ]
    assert len(ordered) == len(set(ordered)) == 4
    assert ordered.index(merge) < ordered.index(main)
    assert ordered.index(merge) < ordered.index(feature)
    assert ordered.index(main) < ordered.index(base)
    assert ordered.index(feature) < ordered.index(base)


def test_topological_history_api_rejects_unsigned_or_tampered_cursor(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    head = _put_commit(store, [], 1, "Head")
    client = _client(store, _History(head, [_history_entry(head, "Head", 1)]), _VersionRefs([]))

    response = client.get(
        "/api/v1/content/project-1/commits",
        params={"order": "topo", "cursor": "f" * 40},
    )

    assert response.status_code == 400
    assert "cursor" in response.json()["detail"]


def test_topological_history_returns_conflict_for_unavailable_signed_snapshot(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    head = _put_commit(store, [], 1, "Head")
    cursor = HistoryCursorCodec("test-history-cursor-secret").encode(HistoryCursorState(
        project_id="project-1",
        snapshot_id="1" * 64,
        roots=(head,),
        head_commit_id=head,
        anchor_commit_id="f" * 40,
    ))
    client = _client(
        store,
        _History(head, [_history_entry(head, "Head", 1)]),
        _VersionRefs([]),
    )

    response = client.get(
        "/api/v1/content/project-1/commits",
        params={"order": "topo", "cursor": cursor},
    )

    assert response.status_code == 409
    assert "refresh the history snapshot" in response.json()["detail"]


def test_topological_cursor_keeps_original_ref_snapshot_after_refs_move(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    base = _put_commit(store, [], 1, "Base")
    main = _put_commit(store, [base], 2, "Main")
    feature = _put_commit(store, [base], 3, "Feature")
    history = _History(main, [
        _history_entry(base, "Base", 1),
        _history_entry(main, "Main", 2),
    ])
    refs = _VersionRefs([
        {"ref_name": "refs/heads/feature", "ref_type": "branch", "commit_id": feature},
    ])
    client = _client(store, history, refs)

    first = client.get(
        "/api/v1/content/project-1/commits",
        params={"order": "topo", "limit": 1},
    ).json()["data"]
    refs.rows = []  # branch deletion after page one must not mutate this walk

    second = client.get(
        "/api/v1/content/project-1/commits",
        params={"order": "topo", "limit": 10, "cursor": first["next_cursor"]},
    )

    assert second.status_code == 200
    page = second.json()["data"]
    combined = [first["commits"][0]["commit_id"], *[row["commit_id"] for row in page["commits"]]]
    assert combined == [main, feature, base]
    assert len(combined) == len(set(combined))
    assert page["snapshot_id"] == first["snapshot_id"]


def test_canonical_root_head_wins_over_newer_global_scope_commit(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    root_head = _put_commit(store, [], 1, "Root head")
    newer_scoped = _put_commit(store, [], 2, "Scoped client head")
    history = _History(
        root_head,
        [_history_entry(root_head, "Root head", 1)],
        global_head_commit_id=newer_scoped,
    )
    response = _client(store, history, _VersionRefs([])).get(
        "/api/v1/content/project-1/commits",
        params={"order": "topo"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["head_commit_id"] == root_head
    assert data["refs"][0] == {
        "ref_name": "refs/heads/main",
        "ref_type": "branch",
        "commit_id": root_head,
    }
    linear = _client(store, history, _VersionRefs([])).get(
        "/api/v1/content/project-1/commits",
        params={"limit": 1},
    )
    assert linear.status_code == 200
    assert linear.json()["data"]["head_commit_id"] == root_head


def test_linear_history_contract_keeps_ascending_catch_up_and_adds_parent_ids(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    base = _put_commit(store, [], 1, "Base")
    middle = _put_commit(store, [base], 2, "Middle")
    head = _put_commit(store, [middle], 3, "Head")
    history = _History(head, [
        _history_entry(base, "Base", 1),
        _history_entry(middle, "Middle", 2),
        _history_entry(head, "Head", 3),
    ])
    client = _client(store, history, _VersionRefs([]))

    latest = client.get(
        "/api/v1/content/project-1/commits",
        params={"limit": 2},
    ).json()["data"]
    assert [commit["commit_id"] for commit in latest["commits"]] == [middle, head]
    assert latest["commits"][0]["parent_ids"] == [base]

    catch_up = client.get(
        "/api/v1/content/project-1/commits",
        params={"limit": 1, "since_commit_id": middle},
    ).json()["data"]
    assert [commit["commit_id"] for commit in catch_up["commits"]] == [head]
    assert catch_up["head_commit_id"] == head


def test_linear_history_does_not_depend_on_named_ref_store_availability(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    head = _put_commit(store, [], 1, "Head")
    history = _History(head, [_history_entry(head, "Head", 1)])
    refs = _VersionRefs([])
    refs.fail = True

    response = _client(store, history, refs).get(
        "/api/v1/content/project-1/commits",
        params={"limit": 1},
    )

    assert response.status_code == 200
    assert response.json()["data"]["head_commit_id"] == head


def test_graph_reports_degraded_health_for_unreadable_named_ref(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    head = _put_commit(store, [], 1, "Head")
    missing = "f" * 40
    history = _History(head, [_history_entry(head, "Head", 1)])
    refs = _VersionRefs([
        {"ref_name": "refs/heads/broken", "ref_type": "branch", "commit_id": missing},
    ])

    response = _client(store, history, refs).get(
        "/api/v1/content/project-1/commits",
        params={"order": "topo"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["graph_health"] == "degraded"
    assert data["unreadable_commit_ids"] == [missing]


def test_graph_reports_degraded_health_for_malformed_commit_ancestry(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    malformed = store.put_commit((
        f"tree {EMPTY_TREE_SHA1}\n"
        "parent not-a-git-object\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        "\n"
    ).encode("utf-8"))
    history = _History(malformed, [_history_entry(malformed, "Malformed", 1)])

    response = _client(store, history, _VersionRefs([])).get(
        "/api/v1/content/project-1/commits",
        params={"order": "topo"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["commits"] == []
    assert data["graph_health"] == "degraded"
    assert data["unreadable_commit_ids"] == [malformed]


def test_graph_uses_a_safe_default_for_an_empty_commit_message(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    head = _put_commit(store, [], 1, "")
    history = _History(head, [_history_entry(head, "", 1)])

    response = _client(store, history, _VersionRefs([])).get(
        "/api/v1/content/project-1/commits",
        params={"order": "topo"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["commits"][0]["message"] == "Update workspace"


def test_graph_fails_closed_when_atomic_ref_snapshot_is_malformed(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    head = _put_commit(store, [], 1, "Head")
    history = _History(head, [_history_entry(head, "Head", 1)])
    refs = _VersionRefs([
        {"ref_name": "refs/tags/not-a-branch", "ref_type": "branch", "commit_id": head},
    ])

    response = _client(store, history, refs).get(
        "/api/v1/content/project-1/commits",
        params={"order": "topo"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "History ref snapshot is invalid"


def test_topological_history_peels_annotated_tag_to_its_commit(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    head = _put_commit(store, [], 1, "Main")
    tagged_commit = _put_commit(store, [], 2, "Tagged orphan")
    tag_body = (
        f"object {tagged_commit}\n"
        "type commit\n"
        "tag v1\n"
        "tagger Test <test@example.com> 3 +0000\n"
        "\nVersion one\n"
    ).encode("utf-8")
    tag_id, tag_loose = encode_object("tag", tag_body)
    store.put_loose(tag_id, tag_loose)
    history = _History(head, [_history_entry(head, "Main", 1)])
    refs = _VersionRefs([
        {"ref_name": "refs/tags/v1", "ref_type": "tag", "commit_id": tag_id},
    ])

    response = _client(store, history, refs).get(
        "/api/v1/content/project-1/commits",
        params={"order": "topo"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert [commit["commit_id"] for commit in data["commits"]] == [head, tagged_commit]
    assert data["refs"][-1] == {
        "ref_name": "refs/tags/v1",
        "ref_type": "tag",
        "commit_id": tagged_commit,
    }
    assert data["graph_health"] == "complete"


def test_graph_traversal_has_a_defensive_commit_budget(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    base = _put_commit(store, [], 1, "Base")
    head = _put_commit(store, [base], 2, "Head")
    history = _History(head, [
        _history_entry(base, "Base", 1),
        _history_entry(head, "Head", 2),
    ])

    response = _client(
        store,
        history,
        _VersionRefs([]),
        max_traversal_nodes=1,
    ).get(
        "/api/v1/content/project-1/commits",
        params={"order": "topo"},
    )

    assert response.status_code == 422
    assert "traversal safety limit" in response.json()["detail"]


def _client(
    store: ObjectStore,
    history: _History,
    refs: _VersionRefs,
    *,
    max_traversal_nodes: int = 200_000,
) -> TestClient:
    repo_manager = _RepoManager(store, history)
    refs.main_commit_id = history._head_commit_id
    history_graph = HistoryGraphService(
        repo_manager,
        refs,
        cursor_codec=HistoryCursorCodec("test-history-cursor-secret"),
        max_traversal_nodes=max_traversal_nodes,
    )
    app = FastAPI()
    app.include_router(history_router, prefix="/api/v1/content")
    app.dependency_overrides[get_repo_manager] = lambda: repo_manager
    app.dependency_overrides[get_version_admin_service] = lambda: VersionAdminService(repo_manager)
    app.dependency_overrides[get_history_graph_service] = lambda: history_graph
    app.dependency_overrides[get_product_operation_adapter] = lambda: _Operations()
    install_authorization(app, authorization_for("project-1", role="viewer"))
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="user-1",
        role="authenticated",
    )
    return TestClient(app)


def _put_commit(
    store: ObjectStore,
    parents: list[str],
    timestamp: int,
    message: str,
) -> str:
    lines = [f"tree {EMPTY_TREE_SHA1}"]
    lines.extend(f"parent {parent}" for parent in parents)
    lines.extend([
        f"author Test Author <author@example.com> {timestamp} +0000",
        f"committer Test Author <author@example.com> {timestamp} +0000",
        "",
        f"{message}\n",
    ])
    return store.put_commit("\n".join(lines).encode())


def _history_entry(commit_id: str, message: str, timestamp: int) -> dict:
    return {
        "commit_id": commit_id,
        "who": "user:test",
        "message": message,
        "changes": [{"path": f"{message.lower().replace(' ', '-')}.md", "action": "update"}],
        "conflicts": [],
        "root_hash": EMPTY_TREE_SHA1,
        "scope_hash": EMPTY_TREE_SHA1,
        "scope_path": "",
        "created_at": f"1970-01-01T00:00:0{timestamp}+00:00",
    }
