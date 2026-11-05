import base64
import hashlib
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from src.platform.hosts.connection_auth import HostConnectionAuth, device_request_message, proof_message

OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEVICE = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CHALLENGE = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
BOOT = UUID("99999999-9999-4999-8999-999999999999")
HELLO = {"protocol": 1, "boot_id": BOOT, "app_version": "1.4.0"}


class DatabaseDenial(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class FakeRepository:
    def __init__(self, public_key):
        self.key = public_key
        self.issued = None
        self.consumed = False
        self.opened = False
        self.revoked = False

    def command(self, name, **params):
        if name == "host_device_key":
            if self.revoked:
                raise DatabaseDenial("42501")
            return {"public_key": self.key.hex(), "revision": 1}
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


def keys():
    private = Ed25519PrivateKey.generate()
    return private, private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def signed(private, timestamp, owner=OWNER, device=DEVICE):
    return base64.b64encode(private.sign(device_request_message(owner, device, timestamp))).decode()


def test_device_signed_request_issues_a_challenge_without_a_person_signed_in():
    private, public = keys()
    repository = FakeRepository(public)
    auth = HostConnectionAuth(repository)
    issued = auth.issue_for_device(OWNER, DEVICE, 1_800_000_000, signed(private, 1_800_000_000), now=1_800_000_030)
    assert repository.issued["p_owner"] == OWNER and repository.issued["p_device"] == str(DEVICE)
    # The usual one-use proof then opens the connection.
    epoch = UUID(issued["epoch"])
    proof = base64.b64encode(private.sign(proof_message(OWNER, DEVICE, CHALLENGE, epoch, issued["nonce"]))).decode()
    assert auth.accept(OWNER, DEVICE, CHALLENGE, epoch, issued["nonce"], proof, "gateway-1", **HELLO) == epoch


@pytest.mark.parametrize("case", ["other_key", "other_owner", "other_time", "garbage"])
def test_device_request_with_a_bad_signature_is_401_and_issues_nothing(case):
    private, public = keys()
    repository = FakeRepository(public)
    signature = {
        "other_key": signed(Ed25519PrivateKey.generate(), 100),
        "other_owner": signed(private, 100, owner="dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        "other_time": signed(private, 101),
        "garbage": "not base64!" * 8,
    }[case]
    with pytest.raises(HTTPException) as denied:
        HostConnectionAuth(repository).issue_for_device(OWNER, DEVICE, 100, signature, now=100)
    # Never 403: the host would forget a key that is still registered.
    assert denied.value.status_code == 401
    assert denied.value.detail == {"code": "device_proof_invalid"}
    assert repository.issued is None


def test_device_request_far_from_server_time_says_what_time_it_is():
    private, public = keys()
    repository = FakeRepository(public)
    with pytest.raises(HTTPException) as skewed:
        HostConnectionAuth(repository).issue_for_device(OWNER, DEVICE, 100, signed(private, 100), now=100 + 301)
    assert skewed.value.status_code == 401
    assert skewed.value.detail == {"code": "device_clock_skew", "server_time": 401}
    assert repository.issued is None


def test_device_request_for_a_revoked_device_is_403():
    private, public = keys()
    repository = FakeRepository(public)
    repository.revoked = True
    with pytest.raises(HTTPException) as denied:
        HostConnectionAuth(repository).issue_for_device(OWNER, DEVICE, 100, signed(private, 100), now=100)
    assert denied.value.status_code == 403
    assert denied.value.detail == {"code": "device_revoked"}
