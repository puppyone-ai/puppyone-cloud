"""Protected environment adapter; cloud credentials never enter public PR jobs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import boto3
import redis
from botocore.config import Config
from botocore.exceptions import ClientError

from .acceptance import accept
from .backup import create_backup
from .object_snapshot import snapshot as snapshot_objects
from .railway import Railway

ROLES = {
    "api",
    "frontend",
    "agent_worker",
    "upload_worker",
    "import_worker",
    "synchronize_worker",
    "mcp_server",
}
# Optional roles join the inventory when the environment deploys them.
OPTIONAL_ROLES = {"ssh_gateway"}
INGRESS_ROLES = ("api", "mcp_server", "frontend", "ssh_gateway")
# Images published by the release workflow's `images` job, pinned by digest.
# "versioning" (default) recovers object bytes from bucket versions. "snapshot"
# is an explicitly approved fallback for providers that cannot version buckets
# (for example Supabase Storage): the release copies and verifies every object
# into the separate private backup bucket after writers stop.
OBJECT_RECOVERY_MODES = {"versioning", "snapshot"}
IMAGE_REPOSITORIES = {
    "backend": "ghcr.io/puppyone-ai/puppyone-cloud-backend",
    "frontend": "ghcr.io/puppyone-ai/puppyone-cloud-frontend",
}


def object_recovery_mode(config: dict) -> str:
    """Validated object recovery mode; absent means bucket versioning."""
    policy = config.get("object_recovery")
    if policy is None:
        return "versioning"
    if not isinstance(policy, dict) or policy.get("mode") not in OBJECT_RECOVERY_MODES:
        raise ValueError("object_recovery.mode must be versioning or snapshot")
    if policy["mode"] == "snapshot" and not all(
        isinstance(policy.get(key), str) and policy[key].strip()
        for key in ("approved_by", "reason")
    ):
        raise ValueError("Snapshot object recovery needs an explicit approver and reason")
    return policy["mode"]


def release_images(images: dict | None, roles=ROLES) -> dict[str, str] | None:
    """Map every role to its digest-pinned image, or None for commit builds."""
    if images is None:
        return None
    if not isinstance(images, dict) or set(images) != set(IMAGE_REPOSITORIES):
        raise ValueError("Release images need the backend and frontend digests")
    for component, repository in IMAGE_REPOSITORIES.items():
        if not re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", images[component]):
            raise ValueError("Release images must be digest-pinned published images")
    return {role: images["frontend" if role == "frontend" else "backend"] for role in sorted(roles)}


class Hosted:
    def __init__(self, root: Path, config: dict, database):
        self.root, self.config, self.db = root, config, database
        roles = set(config["railway"]["services"])
        if not ROLES <= roles <= ROLES | OPTIONAL_ROLES:
            raise ValueError("Complete API, frontend and worker inventory is required")
        self.roles = roles
        ids = [entry["id"] for entry in config["railway"]["services"].values()]
        if len(ids) != len(set(ids)):
            raise ValueError("Release service roles must identify distinct deployments")
        queues = config["queue_names"]
        if (
            not isinstance(queues, list)
            or len(queues) != 3
            or len(set(queues)) != 3
            or any(not isinstance(q, str) or not q.strip() for q in queues)
        ):
            raise ValueError("Declare each of the Upload, Import and Synchronize queues")
        # Reject a malformed recovery policy before any release phase starts.
        object_recovery_mode(config)
        self.images = release_images(config.get("images"), roles)
        self.railway = Railway(config["railway"], config["railway_token"])
        self.redis = redis.Redis.from_url(
            config["redis_url"], socket_timeout=10, socket_connect_timeout=10
        )
        env = config["migration_environment"]
        self.s3 = boto3.client(
            "s3",
            endpoint_url=env["S3_ENDPOINT_URL"],
            region_name=env["S3_REGION"],
            aws_access_key_id=env["S3_ACCESS_KEY_ID"],
            aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"],
            config=Config(signature_version="s3v4", connect_timeout=10, read_timeout=60),
        )

    def preflight(self, source):
        branch = {"staging": "qubits", "production": "main"}[self.config["environment"]]
        if os.environ.get("GITHUB_REF") != "refs/heads/" + branch or os.environ.get(
            "GITHUB_EVENT_NAME"
        ) not in {"push", "workflow_dispatch"}:
            raise ValueError("Hosted release requires its trusted protected branch")
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True
        ).strip()
        if head != source or subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=self.root
        ):
            raise ValueError("Hosted candidate must match its clean source commit")
        self.db.assert_supabase_target(
            project_ref=self.config["supabase_project_id"], api_url=self.config["supabase_url"]
        )
        if (
            self.config["environment"] == "staging"
            and self.config["supabase_project_id"] != "qextonmjqbhxgokmjbio"
        ):
            raise ValueError("Staging release is not bound to the Qubits database")
        if (
            self.config["environment"] == "production"
            and self.config["supabase_project_id"] == "qextonmjqbhxgokmjbio"
        ):
            raise ValueError("Production release cannot target the Qubits database")
        self.railway.preflight(images=self.images)
        self.redis.ping()

    def prepare_recovery(self):
        """Recovery storage for a breaking release; checked before anything stops.

        Ordinary additive releases take no recovery point and never reach this.
        """
        mode = object_recovery_mode(self.config)
        backup_bucket = self.config.get("backup_bucket")
        if not isinstance(backup_bucket, str) or not backup_bucket.strip():
            raise ValueError("A breaking release needs a private versioned backup bucket")
        if backup_bucket == self.config["migration_environment"]["S3_BUCKET_NAME"]:
            raise ValueError("Database backups must use a separate private bucket")
        if mode == "versioning":
            # A DB restore alone cannot recover bytes overwritten/deleted in S3.
            for bucket in (backup_bucket, self.config["migration_environment"]["S3_BUCKET_NAME"]):
                if self.s3.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
                    raise ValueError(
                        "Release recovery requires versioned private object and backup buckets"
                    )
        else:
            # Snapshot recovery copies the object bucket after writers stop;
            # both buckets must at least be reachable before anything stops.
            for bucket in (backup_bucket, self.config["migration_environment"]["S3_BUCKET_NAME"]):
                self.s3.head_bucket(Bucket=bucket)
        self.verify_private_backup_bucket()

    def verify_private_backup_bucket(self):
        bucket = self.config["backup_bucket"]
        public_groups = {
            "http://acs.amazonaws.com/groups/global/AllUsers",
            "http://acs.amazonaws.com/groups/global/AuthenticatedUsers",
        }
        if any(
            grant.get("Grantee", {}).get("URI") in public_groups
            # Supabase Storage answers without a Grants list (no ACL grants).
            for grant in self.s3.get_bucket_acl(Bucket=bucket).get("Grants", [])
        ):
            raise ValueError("Backup bucket ACL is public")
        try:
            policy = self.s3.get_bucket_policy_status(Bucket=bucket)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "NoSuchBucketPolicy":
                raise
        else:
            public = policy.get("PolicyStatus", {}).get("IsPublic")
            if public is True:
                raise ValueError("Backup bucket policy is not private")
            # S3-compatible providers such as Supabase Storage answer this call
            # without IsPublic. Privacy must then be proven by the provider.
            if public is not False and not self.supabase_bucket_is_private(bucket):
                raise ValueError("Backup bucket policy is not private")

    def supabase_bucket_is_private(self, bucket) -> bool:
        """Ask Supabase Storage for the bucket's public flag (service key only)."""
        env = self.config.get("migration_environment", {})
        url, key = env.get("SUPABASE_URL"), env.get("SUPABASE_KEY")
        if not url or not key:
            return False
        request = urllib.request.Request(
            url.rstrip("/") + "/storage/v1/bucket/" + urllib.parse.quote(bucket, safe=""),
            headers={"apikey": key, "Authorization": "Bearer " + key},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                document = json.load(response)
        except (urllib.error.URLError, ValueError):
            return False
        return isinstance(document, dict) and document.get("public") is False

    def prepare_build(self, source):
        if self.images:
            # The images job built, smoke-tested and published these digests
            # for this exact source before this job started; nothing rebuilds.
            return
        # Build before touching the database. Railway deploys the same pinned
        # source through its configured builder, then its SHA is checked again.
        for component in ("backend", "frontend"):
            command = [
                "docker",
                "build",
                "--file",
                str(self.root / component / "Dockerfile.local"),
                "--tag",
                f"puppyone-release-{component}:{source}",
            ]
            if component == "frontend":
                for name, value in self.config["frontend_build_args"].items():
                    if not name.startswith("NEXT_PUBLIC_"):
                        raise ValueError("Only public frontend build arguments are allowed")
                    command += ["--build-arg", name + "=" + value]
            subprocess.run(
                [*command, str(self.root / component)],
                check=True,
                timeout=1800,
                capture_output=True,
            )

    def quiesce(self):
        # Stop incoming requests and periodic producers first; let finite queued
        # work finish while its consumers are still running.
        # Synchronize is a consumer; its periodic producer lives in the API.
        # Keep it alive until its delayed/retry/in-flight queue is empty too.
        roles = getattr(self, "roles", ROLES)
        ingress = [role for role in INGRESS_ROLES if role in roles]
        ingress_evidence = self.railway.stop(ingress)
        deadline = time.monotonic() + self.config.get("drain_timeout_seconds", 900)
        while time.monotonic() < deadline:
            queued = sum(self.redis.zcard(name) for name in self.config["queue_names"])
            active = False
            if self.db.scalar("SELECT to_regclass('public.agent_runs') IS NOT NULL") == "t":
                active = (
                    self.db.scalar(
                        "SELECT EXISTS(SELECT 1 FROM public.agent_runs WHERE state IN ('queued','running','waiting_approval','publishing'))"
                    )
                    == "t"
                )
            if queued == 0 and not active:
                break
            time.sleep(2)
        else:
            raise TimeoutError("Existing work did not drain; schema was not changed")
        workers = sorted(roles - set(ingress))
        worker_evidence = self.railway.stop(workers)
        # Provider-confirmed process exit precedes the lease/in-flight fence.
        deadline = time.monotonic() + self.config.get("drain_timeout_seconds", 900)
        while time.monotonic() < deadline:
            if (
                self.db.scalar("SELECT to_regclass('public.project_write_leases') IS NULL") == "t"
                or self.db.scalar(
                    "SELECT NOT EXISTS(SELECT 1 FROM public.project_write_leases WHERE expires_at>clock_timestamp())"
                )
                == "t"
            ):
                break
            time.sleep(2)
        else:
            raise TimeoutError("Repository write leases did not expire")
        return {
            "environment": self.config["environment"],
            "producer_stop_verified": True,
            "queue_drain_verified": True,
            "old_consumers_exited": True,
            "writers_stopped_at": datetime.now(UTC).isoformat(),
            "records": [
                ingress_evidence,
                worker_evidence,
                {"pending_jobs": 0, "active_agent_runs": 0, "active_write_leases": 0},
            ],
        }

    def backup(self, db):
        mode = object_recovery_mode(self.config)
        evidence = create_backup(db, Path(self.config["private_artifacts"]))
        for field, suffix in (("restore_point_ref", ".dump"), ("restore_roles_ref", ".roles.json")):
            path = Path(evidence[field])
            with path.open("rb") as local:
                expected = hashlib.file_digest(local, "sha256").hexdigest()
            key = f"puppyone-releases/{self.config['environment']}/{evidence['sha256']}{suffix}"
            self.s3.upload_file(
                str(path),
                self.config["backup_bucket"],
                key,
                # Unversioned S3-compatible providers encrypt at rest themselves
                # and may reject the SSE request header.
                ExtraArgs={"ServerSideEncryption": "AES256"} if mode == "versioning" else None,
            )
            result = self.s3.get_object(Bucket=self.config["backup_bucket"], Key=key)
            digest = hashlib.sha256()
            body = result["Body"]
            try:
                while chunk := body.read(1024 * 1024):
                    digest.update(chunk)
            finally:
                body.close()
            if digest.hexdigest() != expected or (
                mode == "versioning" and not result.get("VersionId")
            ):
                raise RuntimeError("Remote backup checksum or version differs")
            evidence[field] = f"s3://{self.config['backup_bucket']}/{key}"
            if mode == "versioning":
                evidence[field + "_version"] = result["VersionId"]
        evidence["objects_before"] = datetime.now(UTC).isoformat()
        evidence["object_recovery_mode"] = mode
        if mode == "snapshot":
            # Writers are stopped (quiesce precedes backup), so the copy is a
            # consistent recovery point for every object a migration may touch.
            evidence["object_recovery_approved_by"] = self.config["object_recovery"]["approved_by"]
            evidence.update(
                snapshot_objects(
                    self.s3,
                    source_bucket=self.config["migration_environment"]["S3_BUCKET_NAME"],
                    target_bucket=self.config["backup_bucket"],
                    prefix=f"puppyone-releases/{self.config['environment']}/{evidence['sha256']}",
                )
            )
        return evidence

    def entrypoint_decisions(self, inventory):
        if not inventory:
            return {"format_version": 1, "rows": []}
        # Reviewed before merge, reapplied only against the exact recorded row
        # fingerprints. Neither a CI flag nor provider config guesses ownership.
        document = self.config.get("entrypoint_decisions")
        if not document:
            raise ValueError("This upgrade has unclassified historical entrypoints")
        return document

    def deploy(self, source):
        self.railway.deploy(source, images=self.images)

    def accept(self):
        return accept(self.config["acceptance"])
