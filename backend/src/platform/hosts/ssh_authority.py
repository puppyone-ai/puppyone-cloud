"""The SSH certificate authority hosts trust for SSH that ends on the host.

The Cloud admits who may reach a device; the host verifies the certificate
itself and the SSH session inside the tunnel is encrypted end to end, so the
gateway relaying it never sees file content, commands or output. Certificates
last minutes: key id ``user:<owner>``, principal = the device ID, no
forwarding, no user rc. The host maps the SSH user name to a registered
folder and applies its own consent and sandbox.
"""

import base64
import logging
import time
from functools import lru_cache
from uuid import UUID

import asyncssh
from fastapi import HTTPException

from src.config import settings

logger = logging.getLogger(__name__)

CERTIFICATE_SECONDS = 300
CLOCK_SLACK_SECONDS = 60


class HostSshAuthority:
    def __init__(self, private_key: str | None = None):
        if private_key:
            key = asyncssh.import_private_key(private_key)
        else:
            if settings.APP_ENV not in {"development", "test"}:
                raise RuntimeError("HOST_SSH_CA_PRIVATE_KEY is required")
            logger.warning("host_ssh_authority_ephemeral")
            key = asyncssh.generate_private_key("ssh-ed25519")
        if key.get_algorithm() != "ssh-ed25519":
            raise RuntimeError("The host SSH authority must be an Ed25519 key")
        self._key = key

    def public_key(self) -> str:
        return self._key.export_public_key("openssh").decode().strip()

    def issue(self, owner: str, device: UUID, client_public_key: str, now: float | None = None) -> dict:
        try:
            user_key = asyncssh.import_public_key(client_public_key)
        except (asyncssh.KeyImportError, ValueError, TypeError) as exc:
            raise HTTPException(422, {"code": "ssh_ed25519_public_key_required"}) from exc
        if user_key.get_algorithm() != "ssh-ed25519":
            raise HTTPException(422, {"code": "ssh_ed25519_public_key_required"})
        current = int(time.time() if now is None else now)
        valid_before = current + CERTIFICATE_SECONDS
        certificate = self._key.generate_user_certificate(
            user_key, f"user:{owner}", principals=[str(device)],
            valid_after=current - CLOCK_SLACK_SECONDS, valid_before=valid_before,
            permit_x11_forwarding=False, permit_agent_forwarding=False, permit_port_forwarding=False,
            permit_user_rc=False, permit_pty=True)
        return {"certificate": certificate.export_certificate("openssh").decode().strip(),
                "valid_before": valid_before}


@lru_cache(maxsize=1)
def host_ssh_authority() -> HostSshAuthority:
    return HostSshAuthority(settings.HOST_SSH_CA_PRIVATE_KEY or None)


def device_host_key(public_key_hex: str) -> str:
    """The device's registered Ed25519 key as an OpenSSH public key: its SSH host key."""
    raw = bytes.fromhex(public_key_hex)
    if len(raw) != 32:
        raise ValueError("Not an Ed25519 public key")
    name = b"ssh-ed25519"
    blob = len(name).to_bytes(4, "big") + name + len(raw).to_bytes(4, "big") + raw
    return f"ssh-ed25519 {base64.b64encode(blob).decode()}"
