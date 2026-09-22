"""
Workspace lower sync worker.

Filesystem folder sync is client-side via Git Remote / AP-FS. This worker only
materializes the current version tree into the local `lower/` cache used by the
workspace provider for external agent workspaces.

Binary files stored as file_ref in hash are resolved by downloading
the actual object from S3, so agents see real files — not JSON stubs.
"""

from __future__ import annotations

import json as _json
import time

from src.infra.s3.service import get_s3_service_instance
from src.platform.workspace.cache import CacheManager
from src.platform.workspace.paths import content_child
from src.utils.logger import log_error, log_info
from src.version_engine.adapters.product.operation_adapter import ProductOperationAdapter


def _extract_file_ref(data: bytes) -> str | None:
    """Return the S3 key from a file_ref blob, or None."""
    try:
        obj = _json.loads(data.decode("utf-8"))
    except Exception:
        return None
    if isinstance(obj, dict) and obj.get("_type") == "file_ref":
        key = obj.get("_s3_key")
        return key if isinstance(key, str) and key else None
    return None


class SyncWorker:
    """
    Materialize a project snapshot from hash into the local lower cache.
    """

    def __init__(self, ops: ProductOperationAdapter | None = None, base_dir: str = "/tmp/contextbase", **kwargs):
        self._ops = ops
        self._cache = CacheManager(base_dir=base_dir)

    def _get_ops(self) -> ProductOperationAdapter:
        if self._ops is not None:
            return self._ops

        from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container

        self._ops = build_worker_version_engine_container().product_operations()
        return self._ops

    async def sync(self, project_id: str | None = None, *args, **kwargs) -> dict:
        if not project_id:
            log_info("[SyncWorker] sync() skipped: project_id missing")
            return {"status": "skipped"}
        return await self.sync_project(project_id, **kwargs)

    async def sync_project(self, project_id: str, **kwargs) -> dict:
        ops = self._get_ops()
        self._cache.clean_project(project_id)
        project_dir = self._cache.get_project_dir(project_id)

        file_count = 0
        dir_count = 0
        binary_count = 0

        try:
            head_commit_id = ops.get_head_commit_id(project_id)
        except Exception:
            head_commit_id = ""

        try:
            entries = ops.list_tree(project_id)
        except Exception as e:
            log_error(f"[SyncWorker] Failed to list version tree for {project_id}: {e}")
            entries = []

        s3 = None  # lazy-init only when a binary is encountered

        for entry in entries:
            local_path = content_child(project_dir, entry.path)
            if entry.type == "folder":
                local_path.mkdir(parents=True, exist_ok=True)
                dir_count += 1
                continue

            try:
                data = ops.read_file(project_id, entry.path)
            except Exception as e:
                log_error(f"[SyncWorker] Failed to read {entry.path}: {e}")
                continue

            s3_key = _extract_file_ref(data)
            if s3_key:
                if s3 is None:
                    s3 = get_s3_service_instance()

                if s3:
                    try:
                        data = await s3.download_file(s3_key)
                        binary_count += 1
                    except Exception as e:
                        log_error(f"[SyncWorker] S3 download failed for {entry.path} ({s3_key}): {e}")

            local_path.parent.mkdir(parents=True, exist_ok=True)
            with local_path.open("wb") as f:
                f.write(data)
            file_count += 1

        self._cache.write_metadata(project_id, {
            "project_id": project_id,
            "head_commit_id": head_commit_id,
            "synced_at": int(time.time()),
            "file_count": file_count,
            "dir_count": dir_count,
            "binary_count": binary_count,
        })

        log_info(
            f"[SyncWorker] Materialized lower cache for {project_id}: "
            f"{file_count} files ({binary_count} binary from S3), {dir_count} dirs, "
            f"commit={head_commit_id or '(none)'}"
        )
        return {
            "status": "ok",
            "head_commit_id": head_commit_id,
            "file_count": file_count,
            "dir_count": dir_count,
            "binary_count": binary_count,
            "path": project_dir,
        }
