"""SSHSIG signatures made in-process, as ``ssh-keygen -Y sign`` makes them (PROTOCOL.sshsig).

Each field can be varied, so a test can build exactly the malformed proof it means.
"""

import base64
import hashlib
import struct

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def pack(value):
    return struct.pack(">I", len(value)) + value


def public_blob(key):
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return pack(b"ssh-ed25519") + pack(raw)


def public_text(key):
    return "ssh-ed25519 " + base64.b64encode(public_blob(key)).decode()


def new_key():
    return Ed25519PrivateKey.generate()


def sshsig(
    key,
    message,
    *,
    namespace=b"puppyone-agent",
    hash_name=b"sha512",
    reserved=b"",
    version=1,
    public=None,
    signer=None,
    trailing=b"",
):
    """The base64 body of an SSHSIG; ``signer`` signs while ``public`` names another key."""
    digest = hashlib.new(hash_name.decode(), message).digest()
    signed = b"SSHSIG" + pack(namespace) + pack(reserved) + pack(hash_name) + pack(digest)
    signature = (signer or key).sign(signed)
    blob = (
        b"SSHSIG"
        + struct.pack(">I", version)
        + pack(public if public is not None else public_blob(key))
        + pack(namespace)
        + pack(reserved)
        + pack(hash_name)
        + pack(pack(b"ssh-ed25519") + pack(signature))
        + trailing
    )
    return base64.b64encode(blob).decode()


def armored(body):
    lines = [body[i : i + 70] for i in range(0, len(body), 70)]
    return (
        "\n".join(["-----BEGIN SSH SIGNATURE-----", *lines, "-----END SSH SIGNATURE-----"]) + "\n"
    )
