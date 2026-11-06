"""The Cloud's SSH certificate authority: what it signs for a person, and what it refuses."""

import base64

import asyncssh
import pytest
from fastapi import HTTPException

from src.config import settings
from src.platform.hosts.ssh_authority import (
    CERTIFICATE_SECONDS,
    CLOCK_SLACK_SECONDS,
    HostSshAuthority,
    device_host_key,
)

OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEVICE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
NOW = 1_800_000_000


def client_key(algorithm="ssh-ed25519"):
    return asyncssh.generate_private_key(algorithm)


def test_a_certificate_is_for_one_owner_and_one_device_for_five_minutes_and_allows_nothing_but_a_pty():
    authority = HostSshAuthority(asyncssh.generate_private_key("ssh-ed25519").export_private_key().decode())
    key = client_key()
    issued = authority.issue(OWNER, DEVICE, key.export_public_key("openssh").decode(), now=NOW)
    certificate = asyncssh.import_certificate(issued["certificate"])
    assert issued["valid_before"] == NOW + CERTIFICATE_SECONDS == NOW + 300
    # asyncssh keeps these as private attributes.
    assert certificate._key_id == f"user:{OWNER}"
    assert certificate.principals == [DEVICE]
    assert certificate._valid_after == NOW - CLOCK_SLACK_SECONDS
    assert certificate._valid_before == NOW + CERTIFICATE_SECONDS
    assert certificate.key.public_data == key.public_data
    assert certificate.signing_key.public_data == asyncssh.import_public_key(authority.public_key()).public_data
    blob = base64.b64decode(issued["certificate"].split()[1])
    # Only permit-pty: no X11, agent or port forwarding, no user rc, and no critical options.
    for refused in (b"permit-X11-forwarding", b"permit-agent-forwarding", b"permit-port-forwarding", b"permit-user-rc"):
        assert refused not in blob
    assert b"permit-pty" in blob and b"force-command" not in blob


@pytest.mark.parametrize("public_key", [
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ",
    "not a key",
    "",
])
def test_only_an_ed25519_client_key_gets_a_certificate(public_key):
    authority = HostSshAuthority(asyncssh.generate_private_key("ssh-ed25519").export_private_key().decode())
    with pytest.raises(HTTPException) as refused:
        authority.issue(OWNER, DEVICE, public_key, now=NOW)
    assert (refused.value.status_code, refused.value.detail) == (422, {"code": "ssh_ed25519_public_key_required"})


def test_an_rsa_client_key_is_refused_even_when_well_formed():
    authority = HostSshAuthority(asyncssh.generate_private_key("ssh-ed25519").export_private_key().decode())
    rsa = client_key("ssh-rsa").export_public_key("openssh").decode()
    with pytest.raises(HTTPException) as refused:
        authority.issue(OWNER, DEVICE, rsa, now=NOW)
    assert refused.value.status_code == 422


def test_the_authority_is_ed25519_and_required_outside_development(monkeypatch):
    with pytest.raises(RuntimeError, match="Ed25519"):
        HostSshAuthority(asyncssh.generate_private_key("ssh-rsa").export_private_key().decode())
    monkeypatch.setattr(settings, "APP_ENV", "staging")
    with pytest.raises(RuntimeError, match="HOST_SSH_CA_PRIVATE_KEY"):
        HostSshAuthority(None)
    monkeypatch.setattr(settings, "APP_ENV", "test")
    assert HostSshAuthority(None).public_key().startswith("ssh-ed25519 ")


def test_the_device_key_becomes_its_ssh_host_key_and_nothing_else_does():
    raw = bytes(range(32))
    line = device_host_key(raw.hex())
    assert asyncssh.import_public_key(line).public_data[-32:] == raw
    assert line.startswith("ssh-ed25519 ")
    with pytest.raises(ValueError):
        device_host_key(bytes(31).hex())
