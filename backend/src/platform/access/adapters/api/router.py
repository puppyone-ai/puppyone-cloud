"""Content API — REST HTTP shell for ProductOperationAdapter.

Provides POSIX-like file system operations (ls, cat, write, mkdir, mv, rm)
on the version content tree. Used by the frontend Web UI and internal services.

Split into sub-modules for maintainability:
  read.py     — ls, cat, stat, tree
  write.py    — write, mkdir, mv, rm, bulk-write
  history.py  — versions, version-content, diff, rollback
"""

from __future__ import annotations

from fastapi import APIRouter

from src.platform.access.adapters.api.history import history_router
from src.platform.access.adapters.api.operations import operations_router
from src.platform.access.adapters.api.read import read_router
from src.platform.access.adapters.api.resource import resource_router
from src.platform.access.adapters.api.write import write_router
from src.platform.access.adapters.api.repository_management import management_router

router = APIRouter(
    prefix="/content",
    tags=["content"],
)

router.include_router(resource_router)
router.include_router(read_router)
router.include_router(write_router)
router.include_router(history_router)
router.include_router(operations_router)
router.include_router(management_router)
