"""Snapshot object recovery for S3-compatible providers without bucket versioning."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.infra.data_migrations.release import hosted as hosted_module
from src.infra.data_migrations.release.hosted import Hosted, object_recovery_mode
from src.infra.data_migrations.release.object_snapshot import restore, snapshot

ROOT = Path(__file__).resolve().parents[4]


class FakeS3:
    """In-memory S3 subset; pages listings two keys at a time to exercise pagination."""

    def __init__(self, buckets, *, versioned=False, page_size=2):
        self.buckets = {name: dict(objects) for name, objects in buckets.items()}
        self.versioned, self.page_size = versioned, page_size
        self.on_get = None
        self.corrupt_uploads = False
        self.upload_args = []

    def _body(self, data):
        stream = io.BytesIO(data)
        return SimpleNamespace(read=stream.read, close=stream.close)

    def list_objects_v2(self, Bucket, MaxKeys=1000, Prefix="", ContinuationToken=None):
        keys = sorted(k for k in self.buckets[Bucket] if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start : start + self.page_size]
        more = start + self.page_size < len(keys)
        return {
            "Contents": [{"Key": k, "Size": len(self.buckets[Bucket][k])} for k in page],
            "IsTruncated": more,
            **({"NextContinuationToken": str(start + self.page_size)} if more else {}),
        }

    def get_object(self, Bucket, Key):
        if self.on_get:
            self.on_get(Bucket, Key)
        data = self.buckets[Bucket][Key]
        return {"Body": self._body(data), **({"VersionId": "v1"} if self.versioned else {})}

    def upload_fileobj(self, fileobj, Bucket, Key):
        data = fileobj.read()
        self.buckets[Bucket][Key] = data + b"!" if self.corrupt_uploads else data

    def upload_file(self, path, Bucket, Key, ExtraArgs=None):
        self.upload_args.append(ExtraArgs)
        self.buckets[Bucket][Key] = Path(path).read_bytes()

    def put_object(self, Bucket, Key, Body):
        self.buckets[Bucket][Key] = Body

    def head_bucket(self, Bucket):
        if Bucket not in self.buckets:
            raise RuntimeError("missing bucket")
        return {}

    def get_bucket_versioning(self, Bucket):
        return {"Status": "Enabled" if self.versioned else "Suspended"}

    def get_bucket_acl(self, Bucket):
        # Supabase Storage answers without a Grants list.
        return {}

    def get_bucket_policy_status(self, Bucket):
        # Supabase Storage answers without IsPublic.
        return {"PolicyStatus": {}}


SOURCE = {
    "version/p1/objects/aa/" + "b" * 38: b"blob-1",
    "projects/p1/uploads/j/file.pdf": b"%PDF" * 1000,
    "mut/p2/x": b"legacy",
    "version/p2/object-bundles/1d/x.pob": b"bundle-bytes",
}


def _hosted(s3, *, recovery=None, backup="release-backups", private=True):
    target = object.__new__(Hosted)
    target.config = {
        "environment": "production",
        "backup_bucket": backup,
        "migration_environment": {
            "S3_BUCKET_NAME": "contextbase",
            "SUPABASE_URL": "https://ref.supabase.co",
            "SUPABASE_KEY": "service-key",
        },
        "private_artifacts": "/unused",
        **({"object_recovery": recovery} if recovery is not None else {}),
    }
    target.s3 = s3
    target.supabase_bucket_is_private = lambda bucket: private
    return target


SNAPSHOT = {
    "mode": "snapshot",
    "approved_by": "operator",
    "reason": "Supabase Storage cannot version buckets",
}


# --- Policy validation


def test_absent_policy_keeps_bucket_versioning():
    assert object_recovery_mode({}) == "versioning"


@pytest.mark.parametrize(
    "policy",
    [
        {"mode": "snapshot"},
        {"mode": "snapshot", "approved_by": " ", "reason": "x"},
        {"mode": "snapshot", "approved_by": "operator"},
        {"mode": "none"},
        "snapshot",
    ],
)
def test_snapshot_needs_explicit_approval_and_known_mode(policy):
    with pytest.raises(ValueError):
        object_recovery_mode({"object_recovery": policy})


def test_github_configuration_rejects_unapproved_snapshot(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "release_actions", ROOT / "supabase/releases/github_actions.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = {
        "format_version": 1,
        "environment": "staging",
        "railway_token": "t",
        "redis_url": "redis://x",
        "railway": {
            "project_id": "p",
            "environment_id": "e",
            "services": {r: {"id": r} for r in module.ROLES},
        },
        "backup_bucket": "b",
        "migration_environment": {},
        "frontend_build_args": {},
        "acceptance": {"api_url": "a", "org_id": "o", "email": "e", "password": "p"},
        "object_recovery": {"mode": "snapshot"},
    }
    source = {
        "TARGET_ENVIRONMENT": "staging",
        "GITHUB_REPOSITORY": "puppyone-ai/puppyone-cloud",
        "GITHUB_REF": "refs/heads/qubits",
        "GITHUB_EVENT_NAME": "push",
        "RELEASE_CONFIG_JSON": json.dumps(config),
        "DATABASE_URL": "d",
        "SUPABASE_URL": "u",
        "SUPABASE_PROJECT_ID": "p",
        "SUPABASE_SERVICE_ROLE_KEY": "k",
        "RUNNER_TEMP": str(tmp_path),
    }
    with pytest.raises(ValueError, match="object_recovery"):
        module.prepare(source)
    config["object_recovery"] = SNAPSHOT
    source["RELEASE_CONFIG_JSON"] = json.dumps(config)
    assert module.prepare(source).exists()


# --- Recovery preflight (runs before anything stops)


def test_snapshot_mode_accepts_unversioned_private_buckets():
    s3 = FakeS3({"contextbase": SOURCE, "release-backups": {}})
    assert _hosted(s3, recovery=SNAPSHOT).prepare_recovery() is None


def test_default_mode_still_requires_versioning():
    s3 = FakeS3({"contextbase": SOURCE, "release-backups": {}})
    with pytest.raises(ValueError, match="versioned"):
        _hosted(s3).prepare_recovery()


@pytest.mark.parametrize(
    "backup,private", [("contextbase", True), ("", True), ("release-backups", False)]
)
def test_snapshot_mode_still_requires_a_separate_private_backup_bucket(backup, private):
    s3 = FakeS3({"contextbase": SOURCE, "release-backups": {}})
    with pytest.raises(ValueError):
        _hosted(s3, recovery=SNAPSHOT, backup=backup, private=private).prepare_recovery()


def test_snapshot_mode_requires_reachable_buckets():
    s3 = FakeS3({"contextbase": SOURCE})
    with pytest.raises(RuntimeError):
        _hosted(s3, recovery=SNAPSHOT).prepare_recovery()


def test_supabase_privacy_is_read_from_the_bucket_public_flag(monkeypatch):
    target = object.__new__(Hosted)
    target.config = {
        "migration_environment": {"SUPABASE_URL": "https://ref.supabase.co", "SUPABASE_KEY": "k"}
    }
    seen = {}

    def answer(public):
        def urlopen(request, timeout):
            seen["url"], seen["auth"] = request.full_url, request.headers.get("Authorization")
            return io.BytesIO(json.dumps({"name": "release-backups", "public": public}).encode())

        return urlopen

    monkeypatch.setattr(hosted_module.urllib.request, "urlopen", answer(False))
    assert target.supabase_bucket_is_private("release-backups") is True
    assert seen == {
        "url": "https://ref.supabase.co/storage/v1/bucket/release-backups",
        "auth": "Bearer k",
    }
    monkeypatch.setattr(hosted_module.urllib.request, "urlopen", answer(True))
    assert target.supabase_bucket_is_private("release-backups") is False
    target.config = {"migration_environment": {}}
    assert target.supabase_bucket_is_private("release-backups") is False


# --- Snapshot and restore


def _manifest(s3, evidence):
    key = evidence["object_snapshot_ref"].split("/", 3)[3]
    return json.loads(s3.buckets["release-backups"][key])


def test_snapshot_copies_and_verifies_every_object():
    s3 = FakeS3({"contextbase": SOURCE, "release-backups": {}})
    evidence = snapshot(
        s3, source_bucket="contextbase", target_bucket="release-backups", prefix="r/production/abc"
    )
    assert evidence["object_snapshot_count"] == len(SOURCE)
    assert evidence["object_snapshot_bytes"] == sum(len(v) for v in SOURCE.values())
    manifest = _manifest(s3, evidence)
    for entry in manifest["objects"]:
        copy = s3.buckets["release-backups"]["r/production/abc/objects/" + entry["key"]]
        assert copy == SOURCE[entry["key"]]
        assert entry["sha256"] == hashlib.sha256(copy).hexdigest()
    raw = s3.buckets["release-backups"]["r/production/abc/objects.manifest.json"]
    assert hashlib.sha256(raw).hexdigest() == evidence["object_snapshot_sha256"]


def test_snapshot_refuses_the_object_bucket_as_its_own_backup():
    s3 = FakeS3({"contextbase": SOURCE})
    with pytest.raises(ValueError):
        snapshot(s3, source_bucket="contextbase", target_bucket="contextbase", prefix="r")


def test_snapshot_detects_an_active_writer():
    s3 = FakeS3({"contextbase": SOURCE, "release-backups": {}})

    def writer(bucket, key):
        if bucket == "contextbase":
            s3.buckets["contextbase"]["version/p3/new"] = b"late write"

    s3.on_get = writer
    with pytest.raises(RuntimeError, match="inventory changed"):
        snapshot(
            s3, source_bucket="contextbase", target_bucket="release-backups", prefix="r", workers=1
        )


def test_snapshot_detects_a_damaged_copy():
    s3 = FakeS3({"contextbase": SOURCE, "release-backups": {}})
    s3.corrupt_uploads = True
    with pytest.raises(RuntimeError, match="verification"):
        snapshot(s3, source_bucket="contextbase", target_bucket="release-backups", prefix="r")


def test_restore_returns_deleted_and_overwritten_objects():
    s3 = FakeS3({"contextbase": SOURCE, "release-backups": {}})
    evidence = snapshot(
        s3, source_bucket="contextbase", target_bucket="release-backups", prefix="r/p/abc"
    )
    # A migration deletes one object, rewrites another and creates a new one.
    del s3.buckets["contextbase"]["mut/p2/x"]
    s3.buckets["contextbase"]["projects/p1/uploads/j/file.pdf"] = b"rewritten"
    s3.buckets["contextbase"]["version/p1/recovery-archive/new"] = b"archive"
    result = restore(
        s3,
        backup_bucket="release-backups",
        manifest_key="r/p/abc/objects.manifest.json",
        expected_sha256=evidence["object_snapshot_sha256"],
        target_bucket="contextbase",
    )
    assert result == {"restored": len(SOURCE), "objects_created_after_snapshot": 1}
    for key, data in SOURCE.items():
        assert s3.buckets["contextbase"][key] == data
    # Objects created after the snapshot are reported, never deleted automatically.
    assert s3.buckets["contextbase"]["version/p1/recovery-archive/new"] == b"archive"


def test_restore_refuses_a_manifest_that_differs_from_release_evidence():
    s3 = FakeS3({"contextbase": SOURCE, "release-backups": {}})
    snapshot(s3, source_bucket="contextbase", target_bucket="release-backups", prefix="r")
    with pytest.raises(RuntimeError, match="manifest"):
        restore(
            s3,
            backup_bucket="release-backups",
            manifest_key="r/objects.manifest.json",
            expected_sha256="0" * 64,
            target_bucket="contextbase",
        )


def test_restore_never_writes_a_damaged_backup_copy():
    s3 = FakeS3({"contextbase": SOURCE, "release-backups": {}})
    evidence = snapshot(
        s3, source_bucket="contextbase", target_bucket="release-backups", prefix="r", workers=1
    )
    damaged = "r/objects/mut/p2/x"
    s3.buckets["release-backups"][damaged] = b"evil!!"  # same length as b"legacy"
    s3.buckets["contextbase"]["mut/p2/x"] = b"current"
    with pytest.raises(RuntimeError, match="manifest"):
        restore(
            s3,
            backup_bucket="release-backups",
            manifest_key="r/objects.manifest.json",
            expected_sha256=evidence["object_snapshot_sha256"],
            target_bucket="contextbase",
            workers=1,
        )
    assert s3.buckets["contextbase"]["mut/p2/x"] == b"current"


# --- Release backup phase


@pytest.fixture
def database_backup(monkeypatch, tmp_path):
    dump, roles = tmp_path / "db.dump", tmp_path / "db.roles.json"
    dump.write_bytes(b"pg dump"), roles.write_bytes(b"{}")
    monkeypatch.setattr(
        hosted_module,
        "create_backup",
        lambda db, path: {
            "restore_point_ref": str(dump),
            "restore_roles_ref": str(roles),
            "sha256": "d" * 64,
            "restore_verified": True,
        },
    )


def test_snapshot_backup_records_database_and_object_recovery(database_backup):
    s3 = FakeS3({"contextbase": SOURCE, "release-backups": {}})
    evidence = _hosted(s3, recovery=SNAPSHOT).backup(db=None)
    assert evidence["object_recovery_mode"] == "snapshot"
    assert evidence["object_recovery_approved_by"] == "operator"
    assert evidence["object_snapshot_count"] == len(SOURCE)
    assert (
        evidence["restore_point_ref"]
        == f"s3://release-backups/puppyone-releases/production/{'d' * 64}.dump"
    )
    assert "restore_point_ref_version" not in evidence
    assert s3.upload_args == [None, None]  # no SSE header for unversioned providers


def test_versioned_backup_still_requires_object_versions(database_backup):
    s3 = FakeS3({"contextbase": SOURCE, "release-backups": {}})
    with pytest.raises(RuntimeError, match="version"):
        _hosted(s3).backup(db=None)
    s3.versioned = True
    evidence = _hosted(s3).backup(db=None)
    assert evidence["object_recovery_mode"] == "versioning"
    assert evidence["restore_point_ref_version"] == "v1"
    assert "object_snapshot_ref" not in evidence
