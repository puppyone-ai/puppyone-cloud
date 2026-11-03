"""Text indexer — populates ``version_text_index`` from new commits.

Contract: ``docs/proposals/PUP-cloud-grep.md`` (§6).

This module is intentionally agnostic about *when* it gets called:

  - **Hot path** — invoked from ``run_post_project_update_hook`` after
    a commit lands so a search query landing a few seconds later sees
    the new content.
  - **Bootstrap path** — invoked from the admin reindex endpoint with
    an explicit ``content_hash`` list when a project pre-dates the
    indexer.

Both go through ``index_blobs`` which is idempotent (Postgres natural
key on ``(project_id, content_hash, chunk_idx)``).

Chunking is line-aligned so ``line_start`` is always a real line
number. Chunk size is ~4 KB, chosen because:
  - bigger → trigram candidate sets blow up per row,
  - smaller → tsvector index bloat,
  - line-aligned because cutting mid-line corrupts the regex
    re-match in the API layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.utils.logger import log_error, log_info, log_warning


# 4 KB chunks balance trigram index density vs row count. Stays under
# the 8 KB Postgres TOAST inline threshold so chunks stay in-heap.
_CHUNK_BYTES = 4096

# Hard cap per single file. Pathologically large text files (gigabyte
# logs, dumps) would otherwise produce millions of index rows; cap so
# the indexer can't OOM the database. Files larger than this index
# only the first ``_MAX_FILE_BYTES`` and surface a warning.
_MAX_FILE_BYTES = 32 * 1024 * 1024  # 32 MB


@dataclass
class IndexableBlob:
    """One unit of work for the indexer.

    The caller resolves these from a commit's change list (hot path)
    or from a content-hash enumeration (bootstrap path). The indexer
    NEVER walks the repository on its own — that's the caller's job
    so the same module works for both flows without separate code
    paths.
    """
    project_id: str
    scope_path: str       # canonical path of the scope this lives under
    file_path: str        # repo-relative
    content_hash: str
    data: bytes           # decoded file bytes (caller's responsibility)


def _chunk_text(text: str) -> list[tuple[int, int, str]]:
    """Cut ``text`` into ~``_CHUNK_BYTES`` line-aligned chunks.

    Returns a list of ``(chunk_idx, line_start, chunk_text)``.
    ``line_start`` is 1-based so the API layer can return user-
    facing line numbers verbatim.
    """
    if not text:
        return []
    chunks: list[tuple[int, int, str]] = []
    buf: list[str] = []
    buf_bytes = 0
    line_start = 1
    cur_line = 1
    chunk_idx = 0
    for line in text.splitlines(keepends=True):
        buf.append(line)
        # The byte count here is the encoded length, which is what
        # determines GIN index payload size. Counting characters
        # would undercount for multibyte text and is therefore wrong.
        buf_bytes += len(line.encode("utf-8", errors="replace"))
        cur_line += 1
        if buf_bytes >= _CHUNK_BYTES:
            chunks.append((chunk_idx, line_start, "".join(buf)))
            chunk_idx += 1
            line_start = cur_line
            buf = []
            buf_bytes = 0
    if buf:
        chunks.append((chunk_idx, line_start, "".join(buf)))
    return chunks


def _decode_for_index(data: bytes) -> str | None:
    """Decode raw bytes to text for indexing.

    Returns ``None`` for content that's almost certainly binary —
    we don't want to feed a JPEG to ``tsvector`` and produce 10,000
    garbage trigrams. Detection mirrors ``access_point_fs._looks_text_*``
    in spirit but is intentionally lighter: it's enough to filter out
    the obvious binaries, not to be a perfect oracle.
    """
    if not data:
        return ""
    if b"\x00" in data[:8192]:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except Exception:
            return None
    if len(text) == 0:
        return ""
    return text


def index_blobs(blobs: Iterable[IndexableBlob]) -> int:
    """Upsert chunks for each blob. Returns total rows written.

    Failures are logged and skipped per blob; one bad file doesn't
    stop the rest. The repository upsert is itself idempotent so a
    re-invocation of the same set is safe.
    """
    from src.version_engine.infrastructure.supabase.text_index_repository import (
        TextIndexRepository,
    )

    repo = TextIndexRepository()
    total = 0
    for blob in blobs:
        try:
            data = blob.data
            if len(data) > _MAX_FILE_BYTES:
                log_warning(
                    f"[TextIndexer] {blob.file_path} is {len(data)} bytes "
                    f"> {_MAX_FILE_BYTES}; truncating for index. "
                    f"Search will only find matches in the first "
                    f"{_MAX_FILE_BYTES // (1024 * 1024)} MB."
                )
                data = data[:_MAX_FILE_BYTES]
            text = _decode_for_index(data)
            if text is None:
                continue
            chunks = _chunk_text(text)
            if not chunks:
                continue
            total += repo.upsert_chunks(
                project_id=blob.project_id,
                scope_path=blob.scope_path,
                file_path=blob.file_path,
                content_hash=blob.content_hash,
                chunks=chunks,
            )
        except Exception as exc:  # noqa: BLE001 — log + continue
            log_error(
                f"[TextIndexer] failed to index "
                f"{blob.project_id}/{blob.file_path} "
                f"({blob.content_hash[:12]}): {exc}"
            )
    return total


def reindex_blobs(
    *,
    project_id: str,
    indexed_commit_id: str,
    blobs: Iterable[IndexableBlob],
) -> int:
    """Bootstrap / admin reindex entry point.

    Indexes a pre-resolved blob list (the caller walks the tree and
    reads bytes) and bumps the project-root freshness watermark to
    ``indexed_commit_id``. Used by the admin text-index rebuild
    endpoint for projects that pre-date the post-commit indexer or
    whose index fell behind. ``index_blobs`` is idempotent, so a
    re-run is safe; the watermark write makes ``grep-indexed`` report
    ``indexed`` for the scope afterwards.
    """
    written = index_blobs(blobs)
    try:
        from src.version_engine.infrastructure.supabase.text_index_repository import (
            TextIndexRepository,
        )
        TextIndexRepository().set_scope_freshness(
            project_id=project_id,
            scope_path="",
            indexed_commit_id=indexed_commit_id,
        )
    except Exception as exc:  # noqa: BLE001
        log_warning(f"[TextIndexer] reindex watermark write failed: {exc}")
    if written:
        log_info(
            f"[TextIndexer] reindexed {written} chunks for project "
            f"{project_id} @ {indexed_commit_id[:12] or 'HEAD'}"
        )
    return written


def _is_delete_change(change: dict) -> bool:
    """True when a change record removes a path.

    The write engine emits two vocabularies: ``op`` (``added`` /
    ``modified`` / ``deleted``, from the tree diff) and ``action``
    (``add`` / ``update`` / ``delete``, from the scoped writer). A
    delete is signalled by either.
    """
    return change.get("action") == "delete" or change.get("op") == "deleted"


def _commit_delta_content_hash(change: dict, data: bytes) -> str:
    """Resolve the content-addressed key for an index row.

    Prefers a hash the change already carries (keeps the hot path and
    the bootstrap reindexer sharing rows). Otherwise computes the real
    Git blob OID from the bytes — ``hash_object("blob", data)`` is the
    same object id the kernel stores under, so identical content across
    commits collapses to one row-set via the ``(project_id,
    content_hash, chunk_idx)`` natural key.

    The previous ``commit_id:path`` surrogate defeated that dedupe: it
    was unique per commit, so every commit re-inserted a fresh row-set
    and the index grew linearly with commit count (GAP-6).
    """
    from src.version_engine.write_engine.git_object_format import hash_object

    carried = change.get("content_hash") or change.get("new_hash")
    if carried:
        return str(carried)
    return hash_object("blob", data)


def _reconcile_index_change(
    repo, *, project_id: str, commit_id: str, path: str, change: dict, read_blob,
) -> tuple["IndexableBlob | None", int]:
    """Reconcile one changed path against the index.

    Returns ``(blob_to_index_or_None, purged_row_count)``. A delete (or a
    path that no longer resolves to a blob) purges its rows and yields no
    blob; a live path has its old rows cleared and yields a blob to index.
    """
    if _is_delete_change(change):
        # Directory deletes carry the directory path, not each child,
        # so purge the whole subtree.
        return None, repo.delete_file(
            project_id=project_id, file_path=path, include_subtree=True,
        )

    # add / update / merge — read the current content.
    try:
        data = read_blob(path)
    except FileNotFoundError:
        data = None
    except Exception as exc:  # noqa: BLE001
        log_warning(
            f"[TextIndexer] read_blob({path}) failed in commit "
            f"{commit_id[:12]}: {exc}"
        )
        return None, 0

    if data is None:
        # The change wasn't tagged as a delete but the path no longer
        # resolves to a blob (raced delete, or a directory entry). Clear
        # any rows it had so search doesn't go stale.
        return None, repo.delete_file(
            project_id=project_id, file_path=path, include_subtree=True,
        )

    # Clear the previous version's rows before re-indexing so a content
    # change can't leave stale chunks searchable. The scope path is ""
    # (project root) because the hook fires at the root; the query side
    # narrows by the AP's scope_path at read time.
    repo.delete_file(project_id=project_id, file_path=path, include_subtree=False)
    return (
        IndexableBlob(
            project_id=project_id,
            scope_path="",
            file_path=path,
            content_hash=_commit_delta_content_hash(change, data),
            data=data,
        ),
        0,
    )


def index_commit_delta(
    *,
    project_id: str,
    commit_id: str,
    changes: list[dict],
    read_blob,
) -> int:
    """Reconcile the text index against one commit's changes.

    Used by the post-commit hook. ``changes`` is the same list shape
    the hook already builds (``{path, action, op, ...}``); ``read_blob``
    is a callable that takes a path and returns bytes (the hook passes
    a closure over its already-resolved repo).

    For every changed path we **clear then re-index** (GAP-6):

      - A deleted path (or a path that no longer resolves to a blob)
        has its rows purged — otherwise ``grep`` keeps returning files
        that were removed.
      - A path that still has content has its previous-version rows
        cleared and the current content re-indexed under the real
        content hash. This keeps the index bounded to the live tree
        instead of accumulating a row-set per historical version, and
        means a modified file no longer surfaces its old contents.

    Returns the count of rows written. Errors are caught internally —
    the post-commit pipeline must NEVER fail because indexing failed.
    """
    if not changes:
        return 0

    from src.version_engine.infrastructure.supabase.text_index_repository import (
        TextIndexRepository,
    )

    repo = TextIndexRepository()

    # De-dup the change list by path (a commit can list a path twice via
    # different scopes); keep the last record so a final delete wins.
    by_path: dict[str, dict] = {}
    for change in changes:
        if isinstance(change, dict) and change.get("path"):
            by_path[change["path"]] = change

    blobs: list[IndexableBlob] = []
    deleted_paths = 0
    for path, change in by_path.items():
        blob, purged = _reconcile_index_change(
            repo,
            project_id=project_id,
            commit_id=commit_id,
            path=path,
            change=change,
            read_blob=read_blob,
        )
        deleted_paths += purged
        if blob is not None:
            blobs.append(blob)

    written = index_blobs(blobs) if blobs else 0

    # Bump the project-root freshness watermark so query callers can tell
    # "this commit is indexed." Always bump — even a delete-only commit
    # advances the index, and leaving the watermark behind would mark the
    # whole scope stale forever. Per-scope watermarks are synthesised
    # from the project-root one at query time when no per-scope row exists.
    try:
        repo.set_scope_freshness(
            project_id=project_id,
            scope_path="",
            indexed_commit_id=commit_id,
        )
    except Exception as exc:  # noqa: BLE001
        log_warning(f"[TextIndexer] freshness watermark write failed: {exc}")

    if written or deleted_paths:
        log_info(
            f"[TextIndexer] commit {commit_id[:12]}: indexed {written} "
            f"chunks across {len(blobs)} files, purged {deleted_paths} "
            f"stale/deleted rows"
        )
    return written
