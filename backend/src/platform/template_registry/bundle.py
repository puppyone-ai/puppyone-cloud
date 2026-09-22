"""Deterministic Template Bundle v1 construction and fail-closed validation."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
import stat
import unicodedata
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError

from .config import TemplateRegistrySettings
from .exceptions import TemplateBundleInvalidError, TemplateBundleTooLargeError
from .schemas import TemplateBundleFile, TemplateBundleManifest, TemplateRelease

_SIGNATURE_DOMAIN = b"papertrain-template-bundle-v1\0"
_CONTENT_DOMAIN = b"papertrain-template-content-v1\0"
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_MANIFEST_NAME = "manifest.json"
_CONTENT_PREFIX = "content/"
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_BLOCKED_SEGMENTS = {
    ".aws",
    ".docker",
    ".git",
    ".gnupg",
    ".kube",
    ".ssh",
    "credentials",
    "secrets",
}
_BLOCKED_FILENAMES = {
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_rsa",
    "id_ed25519",
    "secrets.json",
    "service-account.json",
}
_BLOCKED_SUFFIXES = {".jks", ".key", ".keystore", ".p12", ".pem", ".pfx"}
_PRIVATE_KEY_MARKERS = (
    b"-----BEGIN DSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN PGP PRIVATE KEY BLOCK-----",
)
_FORBIDDEN_FILENAME_CHARACTERS = re.compile(r'[<>:"|?*]')
_WINDOWS_RESERVED_BASENAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


@dataclass(frozen=True)
class BuiltTemplateBundle:
    payload: bytes
    bundle_sha256: str
    manifest: TemplateBundleManifest


@dataclass(frozen=True)
class TemplateBundle:
    manifest: TemplateBundleManifest
    files: dict[str, bytes]
    bundle_sha256: str


def compute_content_sha256(files: dict[str, bytes]) -> str:
    """Hash a sorted path/content inventory without ambiguous concatenation."""

    digest = hashlib.sha256(_CONTENT_DOMAIN)
    for path in sorted(files):
        encoded_path = path.encode("utf-8")
        content = files[path]
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def build_template_bundle(
    *,
    template_id: str,
    release_id: str,
    files: dict[str, bytes],
    settings: TemplateRegistrySettings,
) -> BuiltTemplateBundle:
    """Build reproducible ZIP bytes for trusted local authoring/tests."""

    normalized: dict[str, bytes] = {}
    for path, content in files.items():
        safe_path = validate_content_path(path, settings)
        if safe_path in normalized:
            raise TemplateBundleInvalidError(f"duplicate template path: {safe_path}")
        if not isinstance(content, bytes):
            raise TemplateBundleInvalidError(f"template file {safe_path!r} is not bytes")
        _validate_content_bytes(safe_path, content)
        normalized[safe_path] = content
    _validate_resource_totals(normalized, settings)
    _validate_portable_path_inventory(normalized)
    if not normalized:
        raise TemplateBundleInvalidError("template release contains no files")

    file_records = [
        TemplateBundleFile(
            path=path,
            size=len(normalized[path]),
            sha256=hashlib.sha256(normalized[path]).hexdigest(),
        )
        for path in sorted(normalized)
    ]
    manifest = TemplateBundleManifest(
        format_version=1,
        template_id=template_id,
        release_id=release_id,
        content_sha256=compute_content_sha256(normalized),
        files=file_records,
    )
    manifest_bytes = json.dumps(
        manifest.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        raise TemplateBundleTooLargeError("template manifest exceeds byte limit")

    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_regular_file(archive, _MANIFEST_NAME, manifest_bytes)
        for path in sorted(normalized):
            _write_regular_file(archive, f"{_CONTENT_PREFIX}{path}", normalized[path])
    payload = output.getvalue()
    if len(payload) > settings.TEMPLATE_BUNDLE_MAX_COMPRESSED_BYTES:
        raise TemplateBundleTooLargeError("template bundle exceeds compressed byte limit")
    return BuiltTemplateBundle(
        payload=payload,
        bundle_sha256=hashlib.sha256(payload).hexdigest(),
        manifest=manifest,
    )


def parse_template_bundle(
    *,
    payload: bytes,
    release: TemplateRelease,
    expected_template_id: str,
    settings: TemplateRegistrySettings,
    trusted_public_keys: dict[str, bytes],
    require_signature: bool,
) -> TemplateBundle:
    """Validate and read a complete release without extracting to disk."""

    if len(payload) > settings.TEMPLATE_BUNDLE_MAX_COMPRESSED_BYTES:
        raise TemplateBundleTooLargeError("template bundle exceeds compressed byte limit")
    bundle_sha256 = hashlib.sha256(payload).hexdigest()
    if bundle_sha256 != release.bundle_sha256:
        raise TemplateBundleInvalidError("template bundle SHA-256 does not match release metadata")
    _verify_signature(
        release=release,
        bundle_sha256=bundle_sha256,
        trusted_public_keys=trusted_public_keys,
        required=require_signature,
    )

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload), mode="r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise TemplateBundleInvalidError("template bundle is not a valid ZIP archive") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > settings.TEMPLATE_BUNDLE_MAX_FILES + 64:
            raise TemplateBundleTooLargeError("template bundle contains too many ZIP entries")

        by_name: dict[str, zipfile.ZipInfo] = {}
        expanded_total = 0
        for info in infos:
            if info.filename in by_name:
                raise TemplateBundleInvalidError(f"duplicate ZIP entry: {info.filename}")
            by_name[info.filename] = info
            if info.flag_bits & 0x1:
                raise TemplateBundleInvalidError("encrypted template ZIP entries are not supported")
            unix_mode = info.external_attr >> 16
            file_type = stat.S_IFMT(unix_mode)
            if file_type == stat.S_IFLNK:
                raise TemplateBundleInvalidError(f"symlink ZIP entry is forbidden: {info.filename}")
            if info.is_dir():
                if file_type not in {0, stat.S_IFDIR}:
                    raise TemplateBundleInvalidError(
                        f"non-directory ZIP entry has a directory name: {info.filename}"
                    )
                _validate_directory_entry(info.filename, settings)
                continue
            if file_type not in {0, stat.S_IFREG}:
                raise TemplateBundleInvalidError(
                    f"non-regular ZIP entry is forbidden: {info.filename}"
                )
            if (
                info.file_size > settings.TEMPLATE_BUNDLE_MAX_FILE_BYTES
                and info.filename != _MANIFEST_NAME
            ):
                raise TemplateBundleTooLargeError(
                    f"template file exceeds byte limit: {info.filename}"
                )
            expanded_total += info.file_size
            if expanded_total > settings.TEMPLATE_BUNDLE_MAX_EXPANDED_BYTES + _MAX_MANIFEST_BYTES:
                raise TemplateBundleTooLargeError("template bundle exceeds expanded byte limit")

        manifest_info = by_name.get(_MANIFEST_NAME)
        if manifest_info is None or manifest_info.is_dir():
            raise TemplateBundleInvalidError("template bundle is missing manifest.json")
        manifest_bytes = _read_zip_entry(archive, manifest_info, _MAX_MANIFEST_BYTES)
        try:
            manifest = TemplateBundleManifest.model_validate_json(manifest_bytes)
        except (ValidationError, ValueError) as exc:
            raise TemplateBundleInvalidError("template manifest is invalid") from exc
        if manifest.template_id != expected_template_id:
            raise TemplateBundleInvalidError(
                "template manifest ID does not match the catalog entry"
            )
        if manifest.release_id != release.id:
            raise TemplateBundleInvalidError(
                "template manifest release does not match the catalog entry"
            )

        declared: dict[str, TemplateBundleFile] = {}
        for record in manifest.files:
            safe_path = validate_content_path(record.path, settings)
            if safe_path != record.path:
                raise TemplateBundleInvalidError(f"manifest path is not canonical: {record.path}")
            if safe_path in declared:
                raise TemplateBundleInvalidError(f"duplicate manifest path: {safe_path}")
            declared[safe_path] = record
        if len(declared) > settings.TEMPLATE_BUNDLE_MAX_FILES:
            raise TemplateBundleTooLargeError("template manifest contains too many files")
        _validate_portable_path_inventory(declared)

        content_infos: dict[str, zipfile.ZipInfo] = {}
        for name, info in by_name.items():
            if info.is_dir() or name == _MANIFEST_NAME:
                continue
            if not name.startswith(_CONTENT_PREFIX):
                raise TemplateBundleInvalidError(f"undeclared top-level ZIP entry: {name}")
            safe_path = validate_content_path(name[len(_CONTENT_PREFIX) :], settings)
            if safe_path in content_infos:
                raise TemplateBundleInvalidError(f"duplicate normalized content path: {safe_path}")
            content_infos[safe_path] = info
        if set(content_infos) != set(declared):
            missing = sorted(set(declared) - set(content_infos))
            extra = sorted(set(content_infos) - set(declared))
            raise TemplateBundleInvalidError(
                f"template inventory mismatch (missing={missing[:3]}, extra={extra[:3]})"
            )

        files: dict[str, bytes] = {}
        actual_total = 0
        for path in sorted(declared):
            record = declared[path]
            content = _read_zip_entry(
                archive,
                content_infos[path],
                settings.TEMPLATE_BUNDLE_MAX_FILE_BYTES,
            )
            actual_total += len(content)
            if actual_total > settings.TEMPLATE_BUNDLE_MAX_EXPANDED_BYTES:
                raise TemplateBundleTooLargeError("template content exceeds expanded byte limit")
            if len(content) != record.size:
                raise TemplateBundleInvalidError(f"template file size mismatch: {path}")
            if hashlib.sha256(content).hexdigest() != record.sha256:
                raise TemplateBundleInvalidError(f"template file digest mismatch: {path}")
            _validate_content_bytes(path, content)
            files[path] = content

        if len(files) != release.file_count or actual_total != release.total_bytes:
            raise TemplateBundleInvalidError("release file totals do not match the verified bundle")
        if compute_content_sha256(files) != manifest.content_sha256:
            raise TemplateBundleInvalidError("template aggregate content digest mismatch")
        return TemplateBundle(
            manifest=manifest,
            files=files,
            bundle_sha256=bundle_sha256,
        )


def validate_content_path(path: str, settings: TemplateRegistrySettings) -> str:
    if not isinstance(path, str) or not path:
        raise TemplateBundleInvalidError("template file path must be non-empty text")
    if len(path) > settings.TEMPLATE_BUNDLE_MAX_PATH_LENGTH:
        raise TemplateBundleInvalidError("template file path exceeds length limit")
    if "\\" in path or path.startswith("/") or "\x00" in path:
        raise TemplateBundleInvalidError(f"unsafe template file path: {path!r}")
    if any(ord(character) < 32 for character in path):
        raise TemplateBundleInvalidError(f"control character in template file path: {path!r}")
    if unicodedata.normalize("NFC", path) != path:
        raise TemplateBundleInvalidError(f"template file path must use Unicode NFC: {path!r}")
    if _FORBIDDEN_FILENAME_CHARACTERS.search(path):
        raise TemplateBundleInvalidError(f"non-portable character in template file path: {path!r}")

    pure = PurePosixPath(path)
    parts = pure.parts
    if not parts or len(parts) > settings.TEMPLATE_BUNDLE_MAX_PATH_DEPTH:
        raise TemplateBundleInvalidError(f"template file path depth is invalid: {path!r}")
    if any(part in {"", ".", ".."} for part in parts) or pure.as_posix() != path:
        raise TemplateBundleInvalidError(f"template file path is not canonical: {path!r}")

    lowered_parts = tuple(part.lower() for part in parts)
    filename = lowered_parts[-1]
    for original_part, lowered_part in zip(parts, lowered_parts, strict=True):
        if original_part.endswith((" ", ".")):
            raise TemplateBundleInvalidError(
                f"non-portable trailing character in template file path: {path!r}"
            )
        if lowered_part.split(".", 1)[0] in _WINDOWS_RESERVED_BASENAMES:
            raise TemplateBundleInvalidError(f"reserved filename in template file path: {path!r}")
    if any(part in _BLOCKED_SEGMENTS for part in lowered_parts):
        raise TemplateBundleInvalidError(f"secret-bearing directory is forbidden: {path!r}")
    if filename in _BLOCKED_FILENAMES:
        raise TemplateBundleInvalidError(f"secret-bearing file is forbidden: {path!r}")
    if filename.startswith(".env.") and filename not in {".env.example", ".env.sample"}:
        raise TemplateBundleInvalidError(f"live environment file is forbidden: {path!r}")
    if any(filename.endswith(suffix) for suffix in _BLOCKED_SUFFIXES):
        raise TemplateBundleInvalidError(f"private key material is forbidden: {path!r}")
    return path


def _validate_directory_entry(name: str, settings: TemplateRegistrySettings) -> None:
    if not name.startswith(_CONTENT_PREFIX) or not name.endswith("/"):
        raise TemplateBundleInvalidError(f"unexpected directory ZIP entry: {name}")
    relative = name[len(_CONTENT_PREFIX) : -1]
    if not relative:
        return
    validate_content_path(relative, settings)


def _validate_content_bytes(path: str, content: bytes) -> None:
    if any(marker in content for marker in _PRIVATE_KEY_MARKERS):
        raise TemplateBundleInvalidError(f"private key material is forbidden: {path!r}")


def _validate_portable_path_inventory(records: Mapping[str, object]) -> None:
    """Reject file or directory collisions on case-insensitive filesystems."""

    file_paths = set(records)
    seen: dict[str, str] = {}
    for path in records:
        parts = PurePosixPath(path).parts
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            if depth < len(parts) and prefix in file_paths:
                raise TemplateBundleInvalidError(
                    f"template path is both a file and directory: {prefix!r}"
                )
            key = unicodedata.normalize("NFC", prefix).casefold()
            previous = seen.get(key)
            if previous is not None and previous != prefix:
                raise TemplateBundleInvalidError(
                    f"template paths are not portable: {previous!r} conflicts with {prefix!r}"
                )
            seen[key] = prefix


def signature_message(bundle_sha256: str) -> bytes:
    """Canonical bytes Registry publishers sign for Bundle v1."""

    return _SIGNATURE_DOMAIN + bytes.fromhex(bundle_sha256)


def _write_regular_file(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(filename=name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(info, content)


def _read_zip_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    try:
        with archive.open(info, mode="r") as stream:
            while True:
                chunk = stream.read(min(1024 * 1024, limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise TemplateBundleTooLargeError(
                        f"ZIP entry exceeds byte limit: {info.filename}"
                    )
                chunks.append(chunk)
    except TemplateBundleTooLargeError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise TemplateBundleInvalidError(f"unable to read ZIP entry: {info.filename}") from exc
    return b"".join(chunks)


def _validate_resource_totals(files: dict[str, bytes], settings: TemplateRegistrySettings) -> None:
    if len(files) > settings.TEMPLATE_BUNDLE_MAX_FILES:
        raise TemplateBundleTooLargeError("template release contains too many files")
    total = 0
    for path, content in files.items():
        if len(content) > settings.TEMPLATE_BUNDLE_MAX_FILE_BYTES:
            raise TemplateBundleTooLargeError(f"template file exceeds byte limit: {path}")
        total += len(content)
        if total > settings.TEMPLATE_BUNDLE_MAX_EXPANDED_BYTES:
            raise TemplateBundleTooLargeError("template content exceeds expanded byte limit")


def _verify_signature(
    *,
    release: TemplateRelease,
    bundle_sha256: str,
    trusted_public_keys: dict[str, bytes],
    required: bool,
) -> None:
    if not release.signature:
        if required:
            raise TemplateBundleInvalidError("template release signature is required")
        return
    if not release.signing_key_id:
        raise TemplateBundleInvalidError("signed template release has no signing key ID")
    raw_key = trusted_public_keys.get(release.signing_key_id)
    if raw_key is None:
        raise TemplateBundleInvalidError("template release signing key is not trusted")
    try:
        padded = release.signature + "=" * (-len(release.signature) % 4)
        signature = base64.b64decode(
            padded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
        raise TemplateBundleInvalidError("template release signature is not valid base64") from exc
    try:
        Ed25519PublicKey.from_public_bytes(raw_key).verify(
            signature,
            signature_message(bundle_sha256),
        )
    except (ValueError, InvalidSignature) as exc:
        raise TemplateBundleInvalidError("template release signature verification failed") from exc
