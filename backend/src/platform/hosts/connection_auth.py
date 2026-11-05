"""One-use Ed25519 device proof, separate from a human JWT and Agent credentials."""

import base64
import binascii
import hashlib
import secrets
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException

from .repository import HostRepository


MAX_PROOF_BYTES = 128


def proof_message(owner: str, device: UUID, challenge: UUID, epoch: UUID, nonce: str) -> bytes:
    return (f"puppyone-device-v1\n{owner}\n{device}\n{challenge}\n{epoch}\n{nonce}").encode("utf-8")


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
            raise HTTPException(403, "Device unavailable") from exc
        return {"challenge_id": challenge, "epoch": str(epoch), "nonce": nonce,
                "expires_in": 60}

    def accept(self, owner: str, device: UUID, challenge: UUID, epoch: UUID,
               nonce: str, signature: str, gateway: str):
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
            self.repository.command("host_connection_open", p_device=str(device),
                p_epoch=str(epoch), p_gateway=gateway, p_lease_seconds=30)
            return epoch
        except (ValueError, KeyError, TypeError, binascii.Error, InvalidSignature) as exc:
            raise HTTPException(403, "Invalid device proof") from exc
        except Exception as exc:
            raise HTTPException(403, "Device unavailable") from exc
