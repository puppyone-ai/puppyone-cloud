"""Owner-scoped device registration. Device registration alone grants no file access."""

import base64
import binascii
from uuid import UUID

from fastapi import HTTPException

from .repository import HostRepository


class HostService:
    def __init__(self, repository: HostRepository | None = None):
        self.repository = repository or HostRepository()

    @staticmethod
    def _command(repository: HostRepository, name: str, **params):
        try:
            return repository.command(name, **params)
        except Exception as exc:
            code = getattr(exc, "code", "")
            if code == "42501":
                raise HTTPException(403, "Host access denied") from exc
            if code == "23505":
                raise HTTPException(409, "Host identity already registered") from exc
            if code == "22023":
                raise HTTPException(400, "Invalid host operation") from exc
            raise HTTPException(503, "Host inventory unavailable") from exc

    def register_device(self, owner: str, key: str, name: str, platform: str):
        try:
            public_key = base64.b64decode(key, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(400, "Invalid device public key") from exc
        if len(public_key) != 32:
            raise HTTPException(400, "Invalid device public key")
        return self._command(self.repository, "host_device_register", p_owner=owner,
                             p_key="\\x" + public_key.hex(), p_name=name, p_platform=platform)

    def catalog(self, owner: str):
        return self._command(self.repository, "host_device_catalog", p_owner=owner)

    def register_folder(self, owner: str, device: UUID, folder: UUID, name: str, writable: bool):
        return self._command(self.repository, "host_folder_register", p_owner=owner,
                             p_device=str(device), p_folder=str(folder), p_name=name,
                             p_capabilities=["folder.read", "folder.write"] if writable else ["folder.read"])

    def revoke_device(self, owner: str, device: UUID):
        return self._command(self.repository, "host_device_revoke", p_owner=owner, p_device=str(device))

    def remove_folder(self, owner: str, device: UUID, folder: UUID):
        return self._command(self.repository, "host_folder_remove", p_owner=owner,
                             p_device=str(device), p_folder=str(folder))
