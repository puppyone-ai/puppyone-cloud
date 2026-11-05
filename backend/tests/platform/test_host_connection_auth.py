import base64
import hashlib
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from src.platform.hosts.connection_auth import HostConnectionAuth, proof_message

OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEVICE = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CHALLENGE = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
BOOT = UUID("99999999-9999-4999-8999-999999999999")
HELLO = {"protocol": 1, "boot_id": BOOT, "app_version": "1.4.0"}


class FakeRepository:
    def __init__(self, public_key):
        self.key = public_key
        self.issued = None
        self.consumed = False
        self.opened = False

    def command(self, name, **params):
        if name == "host_challenge_issue":
            self.issued = params
            return str(CHALLENGE)
        if name == "host_challenge_consume":
            if self.consumed or params["p_nonce_hash"] != self.issued["p_nonce_hash"]:
                raise ValueError("replay or wrong nonce")
            self.consumed = True
            return {"public_key": self.key.hex()}
        if name == "host_connection_open":
            self.opened = params
            return {"epoch": params["p_epoch"]}
        raise AssertionError(name)


def test_one_use_device_proof_binds_owner_device_epoch_and_nonce():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    repository = FakeRepository(public)
    auth = HostConnectionAuth(repository)
    challenge = auth.issue(OWNER, DEVICE)
    nonce = challenge["nonce"]
    assert repository.issued["p_nonce_hash"] == "\\x" + hashlib.sha256(base64.b64decode(nonce)).hexdigest()
    epoch = UUID(challenge["epoch"])
    signature = base64.b64encode(private.sign(proof_message(OWNER, DEVICE, CHALLENGE, epoch, nonce))).decode()
    assert auth.accept(OWNER, DEVICE, CHALLENGE, epoch, nonce, signature, "gateway-1", **HELLO) == epoch
    assert (repository.opened["p_protocol"], repository.opened["p_boot_id"],
            repository.opened["p_app_version"]) == (1, str(BOOT), "1.4.0")
    with pytest.raises(HTTPException) as replay:
        auth.accept(OWNER, DEVICE, CHALLENGE, epoch, nonce, signature, "gateway-1", **HELLO)
    assert replay.value.status_code == 403


def test_wrong_signature_does_not_open_connection():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    repository = FakeRepository(public)
    auth = HostConnectionAuth(repository)
    challenge = auth.issue(OWNER, DEVICE)
    signature = base64.b64encode(private.sign(b"different payload")).decode()
    with pytest.raises(HTTPException):
        auth.accept(OWNER, DEVICE, CHALLENGE, UUID(challenge["epoch"]), challenge["nonce"], signature, "gateway-1", **HELLO)
    assert not repository.opened
