"""What proves an HTTPS command: an SSHSIG by the Agent's key over the command and its moment."""

import base64

import pytest

from src.platform.access.adapters.https.signature import (
    MAX_SIGNATURE_CHARS,
    SKEW_SECONDS,
    SignatureRefused,
    check_time,
    command_message,
    signing_key,
)

from .support import armored, new_key, pack, public_blob, public_text, sshsig

CONNECTION = "1fa1d8ab-79bb-4688-9dd6-bebc8afcd33d"
MESSAGE = command_message(CONNECTION, 1795000000, b"pwd && ls")


def refused(signature, message=MESSAGE, code="agent_signature_invalid"):
    with pytest.raises(SignatureRefused) as caught:
        signing_key(signature, message)
    assert caught.value.code == code


def test_the_signed_message_names_the_connection_the_moment_and_the_exact_command():
    assert command_message(CONNECTION, 42, b"echo 'hi'\n") == (
        f"puppyone-agent-exec-v1\n{CONNECTION}\n42\necho 'hi'\n".encode()
    )


def test_a_signature_by_the_key_returns_that_key_in_openssh_form():
    key = new_key()
    assert signing_key(sshsig(key, MESSAGE), MESSAGE) == public_text(key)


def test_the_armor_ssh_keygen_writes_is_accepted_as_well_as_one_line():
    key = new_key()
    body = sshsig(key, MESSAGE)
    assert signing_key(armored(body), MESSAGE) == public_text(key)
    assert signing_key(body, MESSAGE) == public_text(key)


def test_both_hashes_ssh_keygen_may_use_are_accepted_and_no_other():
    key = new_key()
    assert signing_key(sshsig(key, MESSAGE, hash_name=b"sha256"), MESSAGE) == public_text(key)
    refused(sshsig(key, MESSAGE, hash_name=b"sha1"))
    refused(sshsig(key, MESSAGE, hash_name=b"md5"))


def test_a_signature_for_another_message_or_use_is_refused():
    key = new_key()
    refused(sshsig(key, command_message(CONNECTION, 1795000000, b"rm -rf .")))
    refused(sshsig(key, command_message("another-connection", 1795000000, b"pwd && ls")))
    refused(sshsig(key, MESSAGE, namespace=b"file"))
    refused(sshsig(key, MESSAGE, namespace=b"git"))


def test_a_signature_naming_one_key_but_made_by_another_is_refused():
    approved, stranger = new_key(), new_key()
    refused(sshsig(approved, MESSAGE, signer=stranger))
    refused(sshsig(stranger, MESSAGE, public=public_blob(approved)))


def test_only_ed25519_keys_sign():
    key = new_key()
    raw = public_blob(key)[len(pack(b"ssh-ed25519")) :]
    refused(sshsig(key, MESSAGE, public=pack(b"ssh-rsa") + raw))
    refused(sshsig(key, MESSAGE, public=pack(b"ssh-ed25519") + pack(b"\x00" * 31)))


def test_a_malformed_signature_is_refused_not_a_fault():
    key = new_key()
    good = sshsig(key, MESSAGE)
    blob = base64.b64decode(good)
    for broken in (
        "",
        "not base64 ***",
        base64.b64encode(b"NOTSIG" + blob[6:]).decode(),
        base64.b64encode(blob[:40]).decode(),
        base64.b64encode(blob[:-1]).decode(),
        sshsig(key, MESSAGE, trailing=b"\x00"),
        sshsig(key, MESSAGE, version=2),
        sshsig(key, MESSAGE, reserved=b"x"),
        None,
        12,
        "A" * (MAX_SIGNATURE_CHARS + 1),
    ):
        refused(broken)


def test_the_moment_must_be_within_five_minutes_of_the_server():
    now = 1795000000
    assert check_time(str(now), now=now) == now
    assert check_time(str(now - SKEW_SECONDS), now=now) == now - SKEW_SECONDS
    assert check_time(str(now + SKEW_SECONDS), now=now) == now + SKEW_SECONDS
    for far in (now - SKEW_SECONDS - 1, now + SKEW_SECONDS + 1):
        with pytest.raises(SignatureRefused) as caught:
            check_time(str(far), now=now)
        assert caught.value.code == "agent_clock_skew"
    for unreadable in ("", "soon", "1.5", None):
        with pytest.raises(SignatureRefused) as caught:
            check_time(unreadable, now=now)
        assert caught.value.code == "agent_signature_invalid"
