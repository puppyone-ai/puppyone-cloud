"""An Agent proves each HTTPS command with the SSH key it already keeps.

``ssh-keygen -Y sign`` is the only signer an Agent needs, so the proof is an
SSHSIG (OpenSSH PROTOCOL.sshsig) over the command and the moment it was sent.
The private key never leaves the Agent and no bearer secret exists to leak.
"""

import base64
import binascii
import hashlib
import re
import struct
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

NAMESPACE = "puppyone-agent"
# A command signed this far from the server clock is refused, as a device's is.
SKEW_SECONDS = 300
MAX_SIGNATURE_CHARS = 1024
_ARMOR = re.compile(r"-----(BEGIN|END) SSH SIGNATURE-----|\s+")
_HASHES = {"sha256": hashlib.sha256, "sha512": hashlib.sha512}


class SignatureRefused(ValueError):
    """The request does not prove its key; ``code`` says why, for the client."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def command_message(connection, timestamp, command):
    """What the Agent signs: who it is, when, and the exact command bytes."""
    return f"puppyone-agent-exec-v1\n{connection}\n{timestamp}\n".encode() + command


def _string(data, offset):
    if offset + 4 > len(data):
        raise ValueError("Truncated SSH signature")
    (length,) = struct.unpack(">I", data[offset : offset + 4])
    end = offset + 4 + length
    if end > len(data):
        raise ValueError("Truncated SSH signature")
    return data[offset + 4 : end], end


def _pack(value):
    return struct.pack(">I", len(value)) + value


def signing_key(signature, message):
    """Verify an SSHSIG over ``message`` and return the OpenSSH public key that made it.

    Only Ed25519 keys sign, as only they are issued. The caller still has to
    check that the key is the one the connection was approved with.
    """
    if not isinstance(signature, str) or len(signature) > MAX_SIGNATURE_CHARS:
        raise SignatureRefused("agent_signature_invalid")
    try:
        blob = base64.b64decode(_ARMOR.sub("", signature), validate=True)
        if not blob.startswith(b"SSHSIG"):
            raise ValueError("Not an SSH signature")
        (version,) = struct.unpack(">I", blob[6:10])
        public, offset = _string(blob, 10)
        namespace, offset = _string(blob, offset)
        reserved, offset = _string(blob, offset)
        hash_name, offset = _string(blob, offset)
        wrapped, offset = _string(blob, offset)
        if version != 1 or offset != len(blob) or namespace != NAMESPACE.encode() or reserved:
            raise ValueError("Unexpected SSH signature")
        algorithm, rest = _string(public, 0)
        raw_key, end = _string(public, rest)
        if algorithm != b"ssh-ed25519" or end != len(public) or len(raw_key) != 32:
            raise ValueError("Ed25519 key required")
        signed_with, rest = _string(wrapped, 0)
        raw_signature, end = _string(wrapped, rest)
        if signed_with != b"ssh-ed25519" or end != len(wrapped) or len(raw_signature) != 64:
            raise ValueError("Ed25519 signature required")
        digest = _HASHES[hash_name.decode("ascii")](message).digest()
    except (ValueError, KeyError, UnicodeDecodeError, struct.error, binascii.Error) as exc:
        raise SignatureRefused("agent_signature_invalid") from exc
    signed = b"SSHSIG" + _pack(namespace) + _pack(reserved) + _pack(hash_name) + _pack(digest)
    try:
        Ed25519PublicKey.from_public_bytes(raw_key).verify(raw_signature, signed)
    except InvalidSignature as exc:
        raise SignatureRefused("agent_signature_invalid") from exc
    return "ssh-ed25519 " + base64.b64encode(public).decode("ascii")


def check_time(timestamp, now=None):
    """The signed moment as an integer, refused when far from the server clock."""
    try:
        moment = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise SignatureRefused("agent_signature_invalid") from exc
    if abs(int(time.time() if now is None else now) - moment) > SKEW_SECONDS:
        raise SignatureRefused("agent_clock_skew")
    return moment
