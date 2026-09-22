"""
PuppyOneServerRepo — S3/PG adapter for one logical version repository

Backed by S3 ObjectStore + Supabase History/Audit/Scope instead of a local
filesystem.

Key design:
  - Project root is the canonical tree and write CAS boundary.
  - Scope/access state is a path-bounded cache over that root, not authority.
  - Commits are identified by a 40-hex SHA-1 commit_id (the git
    ``commit`` object's hash, stored as a loose object in the project
    ObjectStore). Linear history is preserved by ordering commits on
    (created_at ASC, commit_id ASC).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import ClassVar

from src.version_engine.infrastructure.supabase.audit_backend import SupabaseAuditManager
from src.version_engine.infrastructure.supabase.history_repository import SupabaseHistoryManager
from src.version_engine.infrastructure.supabase.scope_manager import ScopeManager
from src.version_engine.storage.object_store import ObjectStore
from src.version_engine.write_engine.git_object_format import (
    MODE_DIR,
    MODE_FILE,
    TreeEntry,
    encode_tree,
)
from src.version_engine.write_engine.path_utils import normalize_path
from src.version_engine.write_engine.tree import read_tree, tree_to_flat


class PuppyOneServerRepo:
    """Version repository adapter backed by S3 + Supabase.

    All canonical reads go through root_hash for cross-scope visibility.
    Scope rows are maintained for protocol views and legacy compatibility.
    """

    # Process-wide cache for ``list_scope_files`` results, keyed by
    # (project_id, scope_path, scope_hash). Content is immutable
    # under the hash so entries never need invalidation. Bound size
    # so a long-lived process doesn't grow unbounded as scopes
    # advance — older entries fall out FIFO-style.
    #
    # Why class-level rather than instance-level: ``get_server_repo``
    # mints a fresh PuppyOneServerRepo on every API request, but version
    # submissions may read the same scope tree multiple times during merge/CAS
    # retry, AND the next request also benefits from the cache as long as
    # nothing mutated the scope. Putting the dict on the class gives both
    # behaviours from one cache.
    _scope_files_cache: ClassVar[OrderedDict[tuple[str, str, str], dict[str, bytes]]] = (
        OrderedDict()
    )
    _scope_files_cache_lock: ClassVar[threading.Lock] = threading.Lock()
    _SCOPE_FILES_CACHE_MAX: ClassVar[int] = 32

    def __init__(
        self,
        project_id: str,
        project_name: str,
        store: ObjectStore,
        history: SupabaseHistoryManager,
        audit: SupabaseAuditManager,
        scopes: ScopeManager,
    ):
        self._project_id = project_id
        self._project_name = project_name
        self.store = store
        self.history = history
        self.audit = audit
        self.scopes = scopes
        bind_audit = getattr(self.history, "bind_audit_manager", None)
        if callable(bind_audit):
            bind_audit(audit)

        self._pending_scope: dict[str, tuple[str, dict[str, bytes]]] = {}
        self._last_scope_build: tuple[str, str] | None = None

    # ── Project info ──

    def get_project_name(self) -> str:
        return self._project_name

    # ── Scope (delegated to ScopeManager) ──

    def add_scope(self, scope_id: str, path: str, exclude: list | None = None) -> dict:
        return self.scopes.add(scope_id, path, exclude)

    # ── Global Head Commit ──

    def get_head_commit_id(self) -> str:
        return self.history.get_head_commit_id()

    def set_head_commit_id(self, cid: str) -> None:
        self.history.set_head_commit_id(cid)

    # ── Global Root Hash ──
    #
    # Note: only ``cas_update_root_hash`` is exposed on this façade. The
    # unchecked ``set_root_hash`` lives on ``self.history`` and is reserved
    # for the bootstrap path and controlled projection repair. Application code MUST go
    # through CAS — exposing a non-CAS setter on ServerRepo invites
    # lost-update bugs.

    def get_root_hash(self) -> str:
        return self.history.get_root_hash()

    # ── Per-Scope Head + Hash ──
    #
    # Same contract: only the CAS path (``cas_update_scope``) is exposed
    # for the scope hash. ``history.set_scope_hash`` exists for tests and
    # controlled repair paths, and is intentionally not re-exported.

    def get_scope_head_commit_id(self, scope_path: str) -> str:
        return self.history.get_scope_head_commit_id(scope_path)

    def set_scope_head_commit_id(self, scope_path: str, cid: str) -> None:
        self.history.set_scope_head_commit_id(scope_path, cid)

    def get_scope_hash(self, scope_path: str) -> str:
        return self.history.get_scope_hash(scope_path)

    def get_scope_state(self, scope_path: str) -> tuple[str, str]:
        """Return ``(scope_hash, head_commit_id)`` for one scope.

        Supabase can serve this in one query. Older/test backends can keep
        implementing the individual getters and still satisfy this façade.
        """
        get_state = getattr(self.history, "get_scope_state", None)
        if callable(get_state):
            scope_hash, head_commit_id = get_state(scope_path)
            return scope_hash or "", head_commit_id or ""
        return (
            self.get_scope_hash(scope_path) or "",
            self.get_scope_head_commit_id(scope_path) or "",
        )

    def get_all_scope_hashes(self) -> dict[str, str]:
        """Snapshot of materialized scope-cache hashes for this project.

        Returned as ``{scope_path: scope_hash}`` for protocol/read views.
        The canonical write authority is the project root hash; scope rows
        are derived caches over that root and may be rebuilt.
        """
        return self.history.get_all_scope_hashes()

    def cas_update_scope(
        self,
        scope_path: str,
        old_hash: str,
        new_hash: str,
        head_commit_id: str = "",
    ) -> bool:
        """Atomic CAS on (scope_hash, head_commit_id).

        Piggy-backs the matching ``head_commit_id`` onto the same
        Postgres UPDATE so a losing CAS cannot later overwrite the
        winner's head pointer. ``head_commit_id`` is optional only
        for the filesystem interface parity — in practice the push
        handler always passes the fresh commit id it just minted.
        """
        return self.history.cas_update_scope_hash(
            scope_path,
            old_hash,
            new_hash,
            head_commit_id=head_commit_id,
        )

    def cas_update_root_hash(self, old_root: str, new_root: str) -> bool:
        """Atomic CAS on root_hash via Postgres RPC."""
        return self.history.cas_update_root_hash(old_root, new_root)

    # ── History ──

    def record_history(
        self,
        commit_id: str,
        who: str,
        message: str,
        scope_path: str,
        changes: list,
        conflicts: list | None = None,
        scope_hash: str = "",
        root_hash: str = "",
        created_at_iso: str = "",
    ) -> None:
        self.history.record(
            commit_id,
            who,
            message,
            scope_path,
            changes,
            conflicts,
            root_hash=root_hash,
            scope_hash=scope_hash,
            created_at_iso=created_at_iso,
        )

    def publish_project_update(
        self,
        *,
        old_root_hash: str,
        new_root_hash: str,
        commit_id: str,
        who: str,
        message: str,
        changes: list,
        conflicts: list | None,
        created_at_iso: str,
        audit_event_type: str,
        audit_agent_id: str,
        audit_detail: dict,
        source_channel: str = "",
        policy: str = "",
        base_commit_id: str = "",
        client_commit_id: str = "",
        proposed_tree_id: str = "",
        intent_type: str = "operation",
        scope_path: str = "",
        scope_hash: str = "",
        scope_head_commit_id: str = "",
        expected_scope_head_commit_id: str | None = None,
        storage_measurement: dict | None = None,
    ) -> tuple[bool, int | None]:
        """Publish a product-level root transaction.

        Frontend/product API writes and scoped access writes use this path:
        one project-root CAS,
        one history row, one transaction row, one audit row, one outbox row.
        Scope heads are updated inside the same transaction as derived caches;
        they are not the source of truth.
        """

        publish = getattr(self.history, "publish_project_update", None)
        if not callable(publish):
            raise RuntimeError(
                "history backend does not implement publish_project_update; "
                "product-root writes require the atomic publish path"
            )

        publish_kwargs = dict(
            old_root_hash=old_root_hash,
            new_root_hash=new_root_hash,
            scope_path=scope_path,
            scope_hash=scope_hash,
            scope_head_commit_id=scope_head_commit_id,
            expected_scope_head_commit_id=expected_scope_head_commit_id,
            commit_id=commit_id,
            who=who,
            message=message,
            changes=changes,
            conflicts=conflicts,
            created_at_iso=created_at_iso,
            audit_event_type=audit_event_type,
            audit_agent_id=audit_agent_id,
            audit_detail=audit_detail,
            source_channel=source_channel,
            policy=policy,
            base_commit_id=base_commit_id,
            client_commit_id=client_commit_id,
            proposed_tree_id=proposed_tree_id,
            intent_type=intent_type,
        )
        # Keep the disabled-mode call contract identical to the pre-billing
        # publisher. Besides supporting community adapters, this prevents a
        # rollout from requiring every custom history backend to implement a
        # no-op keyword before storage metering is enabled.
        if storage_measurement is not None:
            publish_kwargs["storage_measurement"] = storage_measurement
        return publish(**publish_kwargs)

    def record_version_index(
        self,
        *,
        scope_path: str,
        source_commit_id: str,
        source_scope_hash: str,
        project_root_hash: str,
        project_view_commit_id: str,
    ) -> None:
        record = getattr(self.history, "record_version_index", None)
        if callable(record):
            record(
                scope_path=scope_path,
                source_commit_id=source_commit_id,
                source_scope_hash=source_scope_hash,
                project_root_hash=project_root_hash,
                project_view_commit_id=project_view_commit_id,
            )

    def get_latest_project_view_commit_id(self) -> str:
        latest = getattr(self.history, "get_latest_project_view_commit_id", None)
        return latest() if callable(latest) else ""

    def get_history_since(
        self,
        since_commit_id: str,
        scope_path: str | None = None,
        limit: int = 0,
    ) -> list[dict]:
        return self.history.get_since(since_commit_id, scope_path, limit)

    def get_history_entry(self, commit_id: str) -> dict | None:
        return self.history.get_entry(commit_id)

    # ── Audit (delegate) ──

    def record_audit(self, event_type: str, agent_id: str, detail: dict) -> None:
        self.audit.record(event_type, agent_id, detail)

    def record_scope_sync(
        self,
        *,
        scope_path: str,
        committed_commit_id: str,
        current_head_at_start: str,
        source_commit_id: str,
        actor: str,
        source_channel: str = "scope-sync",
    ) -> None:
        """Record a derived scope-view sync (a non-source scope head advanced
        by project-root projection) as an auditable transaction + audit row.

        Delegates to the history backend; a no-op if the backend predates this
        method. Best-effort — the scope head has already advanced."""
        fn = getattr(self.history, "record_scope_sync", None)
        if callable(fn):
            fn(
                scope_path=scope_path,
                committed_commit_id=committed_commit_id,
                current_head_at_start=current_head_at_start,
                source_commit_id=source_commit_id,
                actor=actor,
                source_channel=source_channel,
            )

    # ── Lock (no-op — CAS replaces locks) ──

    def acquire_lock(self, scope_id: str) -> bool:
        return True

    def release_lock(self, scope_id: str) -> None:
        pass

    # ── File operations (Merkle tree based) ──

    def list_scope_files(self, scope: dict) -> dict[str, bytes]:
        """Read the files visible to a scope from the canonical root.

        Root-first architecture makes the project root the source of truth.
        ``version_scope_state`` is kept as a protocol/cache hint, so it is used only
        as a legacy fallback when the root has not yet been migrated.

        Cached on (project_id, scope_path, scope_hash). The cost we're
        avoiding is one ``read_tree`` per tree node plus one ``store.get``
        per blob — on a project with a 26 MB blob this was a 19-second
        round-trip, hit repeatedly during version merge and scope reads.
        Cache hits return in microseconds.
        """
        scope_path = normalize_path(scope.get("path", ""))
        scope_hash = self._scope_tree_hash_from_root(scope_path)
        if not scope_hash:
            scope_hash = self.get_scope_hash(scope_path)

        if scope_hash:
            cache_key = (self._project_id, scope_path, scope_hash)
            with self._scope_files_cache_lock:
                cached = self._scope_files_cache.get(cache_key)
                if cached is not None:
                    self._scope_files_cache.move_to_end(cache_key)
                    return dict(cached)

        result = self._compute_scope_files(scope, scope_path, scope_hash)

        if scope_hash and result:
            self._cache_scope_files(scope_path, scope_hash, result)

        return result

    def _compute_scope_files(
        self,
        scope: dict,
        scope_path: str,
        scope_hash: str,
    ) -> dict[str, bytes]:
        """Walk the tree and download every reachable blob (slow path).

        Split out from ``list_scope_files`` so the cached fast-path
        is a one-line return at the top.
        """
        if scope_hash and self.store.exists(scope_hash):
            return self._files_from_tree(scope_hash, scope_path, scope)

        return {}

    def _cache_scope_files(
        self,
        scope_path: str,
        scope_hash: str,
        files: dict[str, bytes],
    ) -> None:
        """Insert into the FIFO-bounded scope-files cache. Bytes are
        shared by reference — this is a dict-overhead cache, not a
        bytes-copying one."""
        cache_key = (self._project_id, scope_path, scope_hash)
        with self._scope_files_cache_lock:
            self._scope_files_cache[cache_key] = dict(files)
            self._scope_files_cache.move_to_end(cache_key)
            while len(self._scope_files_cache) > self._SCOPE_FILES_CACHE_MAX:
                self._scope_files_cache.popitem(last=False)

    def _files_from_tree(self, tree_hash: str, scope_path: str, scope: dict) -> dict[str, bytes]:
        excludes = [normalize_path(e) for e in scope.get("exclude", [])]
        flat = tree_to_flat(self.store, tree_hash)
        result: dict[str, bytes] = {}
        for rel_path, blob_hash in flat.items():
            full_rel = f"{scope_path}/{rel_path}" if scope_path else rel_path
            if _is_excluded(full_rel, excludes):
                continue
            result[rel_path] = self.store.get(blob_hash)
        return result

    def write_scope_files(self, scope: dict, files: dict[str, bytes]) -> None:
        key = _scope_key(scope)
        self._pending_scope[key] = (scope.get("path", ""), files)

    def delete_scope_file(self, scope: dict, rel_path: str) -> None:
        pass

    def build_scope_tree(self, scope: dict) -> str:
        key = _scope_key(scope)

        if key in self._pending_scope:
            scope_path, files = self._pending_scope.pop(key)
            tree_hash = self._build_tree_from_files(files)
            self._last_scope_build = (scope_path, tree_hash)
            # We just built a tree from this exact ``files`` mapping
            # under hash ``tree_hash``. Any subsequent push that hits
            # the same scope_hash can return ``files`` directly from
            # the cache instead of re-walking the tree and
            # re-downloading every blob from S3. Saves the second of
            # the two slow passes per push.
            self._cache_scope_files(
                normalize_path(scope_path),
                tree_hash,
                files,
            )
            return tree_hash

        scope_path = normalize_path(scope.get("path", ""))
        subtree_hash = self._scope_tree_hash_from_root(scope_path)
        if subtree_hash:
            return subtree_hash

        scope_hash = self.get_scope_hash(scope_path)
        if scope_hash and self.store.exists(scope_hash):
            return scope_hash

        return self.store.put_tree(encode_tree([]))

    def build_full_tree(self) -> str:
        if self._last_scope_build:
            scope_path, scope_tree_hash = self._last_scope_build
            self._last_scope_build = None
            prefix = scope_path.strip("/") if scope_path else ""
            if not prefix:
                return scope_tree_hash
            root = self.get_root_hash()
            if not root or not self.store.exists(root):
                root = self.store.put_tree(encode_tree([]))
            from src.version_engine.derived.projection import graft_subtree

            return graft_subtree(self.store, root, prefix, scope_tree_hash)

        root = self.get_root_hash()
        if root:
            return root
        return self.store.put_tree(encode_tree([]))

    # ── Internal helpers ──

    def _navigate_to_subtree(self, tree_hash: str, scope_path: str) -> str | None:
        parts = scope_path.split("/") if scope_path else []
        current = tree_hash
        for part in parts:
            if not part:
                continue
            try:
                entries = read_tree(self.store, current)
            except Exception:
                return None
            if part not in entries:
                return None
            typ, h = entries[part]
            if typ != "T":
                return None
            current = h
        return current

    def _scope_tree_hash_from_root(self, scope_path: str) -> str:
        root_hash = self.get_root_hash()
        if not root_hash:
            return ""
        if not scope_path:
            return root_hash if self.store.exists(root_hash) else ""
        subtree_hash = self._navigate_to_subtree(root_hash, scope_path)
        return subtree_hash or ""

    def _build_tree_from_files(self, files: dict[str, bytes]) -> str:
        nested: dict = {}
        for path, content in files.items():
            parts = path.split("/")
            d = nested
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            blob_hash = self.store.put_blob(content)
            d[parts[-1]] = ("B", blob_hash)
        return _write_nested_tree(self.store, nested)


def _scope_key(scope: dict) -> str:
    return scope.get("id", scope.get("path", "_default"))


def _is_excluded(full_rel: str, excludes: list[str]) -> bool:
    return any(full_rel.startswith(exc + "/") or full_rel == exc for exc in excludes)


def _write_nested_tree(store: ObjectStore, node: dict) -> str:
    """Build a git tree object from a nested ``{name: ("B"|"T", hash) | dict}``
    structure.

    Kept local so repository tree writes stay inside PuppyOne-owned
    Git object utilities.
    """
    entries: list[TreeEntry] = []
    for name, val in sorted(node.items()):
        if isinstance(val, tuple):
            kind, sub_hash = val
            entries.append(
                TreeEntry(
                    name=name,
                    mode=MODE_FILE if kind == "B" else MODE_DIR,
                    sha1_hex=sub_hash,
                )
            )
        else:
            sub_hash = _write_nested_tree(store, val)
            entries.append(TreeEntry(name=name, mode=MODE_DIR, sha1_hex=sub_hash))
    return store.put_tree(encode_tree(entries))
