"""One-use Ed25519 device proof, separate from a human JWT and Agent credentials."""

import base64
import binascii
import hashlib
import secrets
import time
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException

from .repository import HostRepository

MAX_PROOF_BYTES = 128
# A device-signed challenge request is accepted this far from the server clock.
DEVICE_REQUEST_SKEW_SECONDS = 300


def proof_message(owner: str, device: UUID, challenge: UUID, epoch: UUID, nonce: str) -> bytes:
    return (f"puppyone-device-v1\n{owner}\n{device}\n{challenge}\n{epoch}\n{nonce}").encode()


def device_request_message(owner: str, device: UUID, timestamp: int) -> bytes:
    """What a host signs to ask for a challenge with no signed-in person on it."""
    return f"puppyone-device-challenge-v1\n{owner}\n{device}\n{timestamp}".encode()


class HostConnectionAuth:
    def __init__(self, repository: HostRepository | None = None):
        self.repository = repository or HostRepository()

    def issue(self, owner: str, device: UUID):
        nonce = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        epoch = uuid4()
        digest = hashlib.sha256(base64.b64decode(nonce)).hexdigest()
        try:
            challenge = self.repository.command("host_challenge_issue", p_owner=owner,
                p_device=str(device), p_epoch=str(epoch), p_nonce_hash="\\x" + digest)
        except Exception as exc:
            # Only a definite denial is 403: a Desktop forgets a revoked device's
            # key, so an outage must never look like revocation.
            if getattr(exc, "code", "") == "42501":
                raise HTTPException(403, {"code": "device_revoked"}) from exc
            if getattr(exc, "code", "") == "22023":
                raise HTTPException(429, {"code": "device_challenge_limit"}) from exc
            raise HTTPException(503, {"code": "device_unavailable"}) from exc
        return {"challenge_id": challenge, "epoch": str(epoch), "nonce": nonce,
                "expires_in": 60}

    def issue_for_device(self, owner: str, device: UUID, timestamp: int, signature: str,
                         now: float | None = None):
        """A challenge for a host that proves its device key instead of a human JWT.

        A bad signature or clock is 401 so the host keeps its key and retries;
        only a device that is not this owner's active device is 403 (revoked).
        """
        current = int(time.time() if now is None else now)
        if abs(current - timestamp) > DEVICE_REQUEST_SKEW_SECONDS:
            raise HTTPException(401, {"code": "device_clock_skew", "server_time": current})
        try:
            raw_signature = base64.b64decode(signature, validate=True)
            if len(raw_signature) != 64 or len(signature) > MAX_PROOF_BYTES:
                raise ValueError("Bad signature size")
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(401, {"code": "device_proof_invalid"}) from exc
        try:
            record = self.repository.command("host_device_key", p_owner=owner, p_device=str(device))
        except Exception as exc:
            if getattr(exc, "code", "") == "42501":
                raise HTTPException(403, {"code": "device_revoked"}) from exc
            raise HTTPException(503, {"code": "device_unavailable"}) from exc
        try:
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(record["public_key"]))
            key.verify(raw_signature, device_request_message(owner, device, timestamp))
        except (ValueError, KeyError, TypeError, InvalidSignature) as exc:
            raise HTTPException(401, {"code": "device_proof_invalid"}) from exc
        return self.issue(owner, device)

    def accept(self, owner: str, device: UUID, challenge: UUID, epoch: UUID,
               nonce: str, signature: str, gateway: str, *, protocol: int, boot_id: UUID,
               app_version: str):
        try:
            raw_nonce = base64.b64decode(nonce, validate=True)
            raw_signature = base64.b64decode(signature, validate=True)
            if len(raw_nonce) != 32 or len(raw_signature) != 64 or len(signature) > MAX_PROOF_BYTES:
                raise ValueError("Bad proof size")
            result = self.repository.command("host_challenge_consume", p_owner=owner,
                p_device=str(device), p_challenge=str(challenge), p_epoch=str(epoch),
                p_nonce_hash="\\x" + hashlib.sha256(raw_nonce).hexdigest())
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(result["public_key"]))
            key.verify(raw_signature, proof_message(owner, device, challenge, epoch, nonce))
            # The challenge was consumed above; these are the signed-in host's own
            # description, validated by the gateway and again by the database.
            self.repository.command("host_connection_open", p_device=str(device),
                p_epoch=str(epoch), p_gateway=gateway, p_lease_seconds=30,
                p_protocol=protocol, p_boot_id=str(boot_id), p_app_version=app_version)
            return epoch
        except (ValueError, KeyError, TypeError, binascii.Error, InvalidSignature) as exc:
            raise HTTPException(403, "Invalid device proof") from exc
        except Exception as exc:
            raise HTTPException(403, "Device unavailable") from exc
