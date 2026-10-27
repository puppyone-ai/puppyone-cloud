"""Typed errors for scoped filesystem commands."""

from __future__ import annotations


class ScopedFsError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ScopedFsNotFound(ScopedFsError):
    def __init__(self, message: str):
        super().__init__("NOT_FOUND", message, status_code=404)


class ScopedFsPermissionDenied(ScopedFsError):
    def __init__(self, message: str):
        super().__init__("PERMISSION_DENIED", message, status_code=403)
