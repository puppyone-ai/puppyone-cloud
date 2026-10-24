"""Rebuild derived indexes from native ref events, with fenced retries.

Synchronous steps; the scheduler job (infra/scheduler/jobs/native_projection_job)
orchestrates them and broadcasts the WebSocket notification.

Only the maintenance worker uses the internal read-pin capability. API readers
continue to require their current Human/Runtime grant. Event claims never grant
object publication rights. A stale projection cannot overwrite a newer ref view.
"""

from __future__ import annotations

import json
import uuid

from src.config import settings
from src.infra.search.text_indexer import _chunk_text, _decode_for_index
from src.version_engine.infrastructure.supabase.ref_authority_repository import (
    RefAuthorityRepository,
)
from src.version_engine.read.native_tree_reader import NativeTreeReader
from src.version_engine.read.repository_snapshot import RepositorySnapshot
from src.version_engine.storage.backends.s3 import S3StorageBackend


def rebuild_projection(manager, project, token):
    control = RefAuthorityRepository(manager._supabase.client)
    pin = str(uuid.uuid4())
    actor = "maintenance:native-projection"
    wire = control.begin_read(project, actor, pin)
    backend = S3StorageBackend(manager._s3, project, supabase=manager._supabase)
    snapshot = RepositorySnapshot(
        control,
        backend,
        project_id=project,
        actor=actor,
        pin=pin,
        wire=wire,
        max_bytes=settings.GIT_MAX_EXPANDED_BYTES,
    )
    try:
        reader = NativeTreeReader(snapshot)
        paths, chunks, indexed, budget = [], [], set(), 0
        for entry in reader.list_tree(project):
            if entry.type in {"folder", "gitlink"}:
                continue
            # SQL text cannot represent arbitrary Git path bytes. Such names
            # remain fully accessible through Git and the native Product API.
            try:
                entry.path.encode("utf-8")
            except UnicodeEncodeError:
                continue
            body = reader.read_file(project, entry.path)
            row = dict(
                path=entry.path, oid=entry.content_hash, bytes=len(body), mime=entry.mime_type or ""
            )
            paths.append(row)
            budget += len(json.dumps(row).encode())
            if budget > 32 * 1024**2:
                raise ValueError("native projection budget exceeded; index remains stale")
            if entry.content_hash in indexed:
                continue
            indexed.add(entry.content_hash)
            text = _decode_for_index(body)
            if text is None:
                continue
            for index, line, content in _chunk_text(text):
                row = dict(
                    path=entry.path,
                    oid=entry.content_hash,
                    chunk_idx=index,
                    line_start=line,
                    text=content,
                )
                budget += len(json.dumps(row).encode())
                if budget > 32 * 1024**2:
                    raise ValueError("native projection budget exceeded; index remains stale")
                chunks.append(row)
        head = reader.get_head_commit_id(project)
        control.call(
            "replace_native_projection",
            p_project=project,
            p_token=token,
            p_sequence=snapshot.ref_sequence,
            p_head=head,
            p_paths=paths,
            p_chunks=chunks,
        )
        return head, snapshot.ref_sequence
    finally:
        snapshot.close()


def claim_native_events(manager, token: str, limit: int) -> list[dict]:
    control = RefAuthorityRepository(manager._supabase.client)
    return control.call("claim_native_projection_events", p_token=token, p_limit=limit)


def finish_native_event(manager, project: str, token: str, sequence) -> None:
    RefAuthorityRepository(manager._supabase.client).call(
        "finish_native_projection_events",
        p_project=project,
        p_token=token,
        p_sequence=sequence,
    )


def release_native_event(manager, project: str, token: str) -> None:
    RefAuthorityRepository(manager._supabase.client).call(
        "release_native_projection_events", p_project=project, p_token=token
    )
