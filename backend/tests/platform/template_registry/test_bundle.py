from __future__ import annotations

import base64
import hashlib
import io
import json
import stat
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.platform.template_registry.bundle import (
    build_template_bundle,
    parse_template_bundle,
    signature_message,
    validate_content_path,
)
from src.platform.template_registry.config import TemplateRegistrySettings
from src.platform.template_registry.exceptions import TemplateBundleInvalidError
from src.platform.template_registry.schemas import TemplateRelease


def _settings(**overrides) -> TemplateRegistrySettings:
    return TemplateRegistrySettings(_env_file=None, **overrides)


def _release(bundle, **overrides) -> TemplateRelease:
    values = {
        "id": "1.2.3",
        "version": "1.2.3",
        "bundle_sha256": bundle.bundle_sha256,
        "file_count": len(bundle.manifest.files),
        "total_bytes": sum(item.size for item in bundle.manifest.files),
    }
    values.update(overrides)
    return TemplateRelease(**values)


def test_bundle_is_deterministic_and_round_trips() -> None:
    settings = _settings()
    files = {
        "README.md": b"# Hello\n",
        "data/example.json": b'{"ok":true}\n',
        "empty.txt": b"",
    }

    first = build_template_bundle(
        template_id="hello", release_id="1.2.3", files=files, settings=settings
    )
    second = build_template_bundle(
        template_id="hello",
        release_id="1.2.3",
        files=dict(reversed(list(files.items()))),
        settings=settings,
    )

    assert first.payload == second.payload
    parsed = parse_template_bundle(
        payload=first.payload,
        release=_release(first),
        expected_template_id="hello",
        settings=settings,
        trusted_public_keys={},
        require_signature=False,
    )
    assert parsed.files == files


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "/absolute.txt",
        "folder\\windows.txt",
        ".git/config",
        ".env",
        "secrets/token.txt",
        "keys/private.pem",
        "folder/bad:name.txt",
        "folder/NUL.txt",
        "folder/trailing. ",
        "Cafe\u0301/readme.md",
    ],
)
def test_bundle_rejects_unsafe_and_secret_bearing_paths(path: str) -> None:
    with pytest.raises(TemplateBundleInvalidError):
        validate_content_path(path, _settings())


def test_bundle_rejects_archive_digest_tampering() -> None:
    settings = _settings()
    bundle = build_template_bundle(
        template_id="hello",
        release_id="1.2.3",
        files={"README.md": b"hello"},
        settings=settings,
    )
    tampered = bundle.payload + b"trailing-data"

    with pytest.raises(TemplateBundleInvalidError, match="SHA-256"):
        parse_template_bundle(
            payload=tampered,
            release=_release(bundle),
            expected_template_id="hello",
            settings=settings,
            trusted_public_keys={},
            require_signature=False,
        )


def test_bundle_allows_a_release_containing_only_an_empty_file() -> None:
    settings = _settings()
    bundle = build_template_bundle(
        template_id="empty",
        release_id="1.0.0",
        files={"empty.txt": b""},
        settings=settings,
    )

    parsed = parse_template_bundle(
        payload=bundle.payload,
        release=_release(bundle, id="1.0.0", version="1.0.0"),
        expected_template_id="empty",
        settings=settings,
        trusted_public_keys={},
        require_signature=False,
    )

    assert parsed.files == {"empty.txt": b""}


@pytest.mark.parametrize(
    "files",
    [
        {"README.md": b"one", "readme.md": b"two"},
        {"Docs/one.md": b"one", "docs/two.md": b"two"},
    ],
)
def test_bundle_rejects_case_insensitive_path_collisions(files: dict[str, bytes]) -> None:
    with pytest.raises(TemplateBundleInvalidError, match="not portable"):
        build_template_bundle(
            template_id="hello",
            release_id="1.2.3",
            files=files,
            settings=_settings(),
        )


def test_bundle_rejects_a_path_used_as_both_file_and_directory() -> None:
    with pytest.raises(TemplateBundleInvalidError, match="both a file and directory"):
        build_template_bundle(
            template_id="hello",
            release_id="1.2.3",
            files={"docs": b"file", "docs/readme.md": b"nested"},
            settings=_settings(),
        )


def test_bundle_rejects_private_key_markers_inside_otherwise_safe_files() -> None:
    with pytest.raises(TemplateBundleInvalidError, match="private key"):
        build_template_bundle(
            template_id="hello",
            release_id="1.2.3",
            files={"notes.txt": b"-----BEGIN PRIVATE KEY-----\nsecret"},
            settings=_settings(),
        )


def test_remote_signature_is_verified_against_operator_keyring() -> None:
    settings = _settings()
    bundle = build_template_bundle(
        template_id="hello",
        release_id="1.2.3",
        files={"README.md": b"hello"},
        settings=settings,
    )
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    signature = (
        base64.urlsafe_b64encode(private_key.sign(signature_message(bundle.bundle_sha256)))
        .decode("ascii")
        .rstrip("=")
    )
    release = _release(
        bundle,
        signing_key_id="registry-2026",
        signature=signature,
    )

    parsed = parse_template_bundle(
        payload=bundle.payload,
        release=release,
        expected_template_id="hello",
        settings=settings,
        trusted_public_keys={"registry-2026": public_key},
        require_signature=True,
    )
    assert parsed.bundle_sha256 == bundle.bundle_sha256

    with pytest.raises(TemplateBundleInvalidError, match="not trusted"):
        parse_template_bundle(
            payload=bundle.payload,
            release=release,
            expected_template_id="hello",
            settings=settings,
            trusted_public_keys={},
            require_signature=True,
        )

    with pytest.raises(TemplateBundleInvalidError, match="valid base64"):
        parse_template_bundle(
            payload=bundle.payload,
            release=_release(
                bundle,
                signing_key_id="registry-2026",
                signature="not!base64",
            ),
            expected_template_id="hello",
            settings=settings,
            trusted_public_keys={"registry-2026": public_key},
            require_signature=True,
        )


def test_bundle_rejects_symlink_entries_before_extraction() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"not": "trusted"}))
        link = zipfile.ZipInfo("content/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, b"target")
    payload = output.getvalue()
    release = TemplateRelease(
        id="1.2.3",
        version="1.2.3",
        bundle_sha256=hashlib.sha256(payload).hexdigest(),
        file_count=1,
        total_bytes=6,
    )

    with pytest.raises(TemplateBundleInvalidError, match="symlink"):
        parse_template_bundle(
            payload=payload,
            release=release,
            expected_template_id="hello",
            settings=_settings(),
            trusted_public_keys={},
            require_signature=False,
        )


def test_bundle_rejects_non_regular_entries_before_extraction() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"not": "trusted"}))
        device = zipfile.ZipInfo("content/device")
        device.create_system = 3
        device.external_attr = (stat.S_IFCHR | 0o666) << 16
        archive.writestr(device, b"")
    payload = output.getvalue()
    release = TemplateRelease(
        id="1.2.3",
        version="1.2.3",
        bundle_sha256=hashlib.sha256(payload).hexdigest(),
        file_count=1,
        total_bytes=0,
    )

    with pytest.raises(TemplateBundleInvalidError, match="non-regular"):
        parse_template_bundle(
            payload=payload,
            release=release,
            expected_template_id="hello",
            settings=_settings(),
            trusted_public_keys={},
            require_signature=False,
        )
