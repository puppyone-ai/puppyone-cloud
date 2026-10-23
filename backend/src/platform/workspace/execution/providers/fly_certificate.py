"""Present an unmodified, app-restricted Fly user certificate with AsyncSSH.

Fly adds allowed-apps@fly.io, which generic SSH certificate validators do not
understand. Cryptography verifies the signed certificate; we check that exact
restriction before passing its original bytes to AsyncSSH. The Fly SSH server
also enforces the restriction. This adapter never validates server host keys.
"""

import base64
import json
import time

import asyncssh
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    SSHCertificate,
    SSHCertificateType,
    load_ssh_public_identity,
)


def import_user_certificate(value: str, key, app: str):
    certificate = load_ssh_public_identity(value.encode())
    if not isinstance(certificate, SSHCertificate):
        raise ValueError("Fly did not issue a certificate")
    certificate.verify_cert_signature()
    if (
        certificate.type != SSHCertificateType.USER
        or set(certificate.valid_principals) != {b"root", b"fly"}
        or set(certificate.critical_options) != {b"allowed-apps@fly.io"}
        or json.loads(certificate.critical_options[b"allowed-apps@fly.io"]) != [app]
        or not certificate.valid_after <= time.time() < certificate.valid_before
    ):
        raise ValueError("Fly SSH certificate scope or validity mismatch")
    public = asyncssh.import_public_key(
        certificate.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH)
    )
    algorithm, encoded = certificate.public_bytes().split()[:2]
    if public.public_data != key.public_data or algorithm != b"ssh-ed25519-cert-v01@openssh.com":
        raise ValueError("Fly SSH certificate key mismatch")
    return asyncssh.SSHCertificate(
        algorithm,
        public.sig_algorithms,
        public.cert_algorithms,
        public,
        base64.b64decode(encoded, validate=True),
        None,
    )
