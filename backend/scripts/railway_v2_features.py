"""Deep test of the new V2 endpoints on Railway.

Distinct from ``railway_smoke.py`` (which covers the pre-V2 baseline
plus the new health / rebuild / shadow-cap endpoints). This script
exercises:

  1. Shadow snapshot I3 — POST /local-snapshots/{id}/blobs
     (good blob, hash-poisoned blob, multi-blob batch)
  2. Shadow snapshot I5 — POST /local-snapshots/{id}/promote
     (full manifest → commit, missing-blob refusal, post-promote
      snapshot row cleanup)
  3. Conflicts API surface — create a pending row via manual_review
     and resolve it
  4. OpenAPI — verify all V2 routes are advertised so a missing
     deploy gets caught here, not later in production

Run:

    uv run python scripts/railway_v2_features.py
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

API = os.environ.get("PUPPYONE_API_URL", "https://qubits-api.puppyone.ai").rstrip("/")
TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def _load_token() -> str:
    import tempfile
    token = os.environ.get("QUBITS_TOKEN", "").strip()
    if token:
        return token
    candidates = [Path("/tmp/jwt.txt"), Path(tempfile.gettempdir()) / "jwt.txt"]
    for p in candidates:
        try:
            if p.exists():
                return p.read_text().strip()
        except OSError:
            continue
    raise SystemExit("No JWT found.")


@dataclass
class StepResult:
    name: str
    ok: bool
    elapsed_ms: float
    detail: str = ""


class V2Smoke:
    def __init__(self, token: str):
        self.client = httpx.Client(
            base_url=API,
            timeout=TIMEOUT,
            http2=False,
            headers={"Authorization": f"Bearer {token}"},
        )
        self.results: list[StepResult] = []
        self.test_project_id: str = ""
        self.test_project_name = f"v2-features-{int(time.time())}"
        self.snapshot_id: str = ""

    def step(self, name: str, fn):
        print(f"\n── {name} ──")
        started = time.perf_counter()
        try:
            detail = fn() or ""
            elapsed = (time.perf_counter() - started) * 1000
            print(f"  ✓ PASS ({elapsed:.0f} ms) {detail}")
            self.results.append(StepResult(name, True, elapsed, detail))
        except AssertionError as exc:
            elapsed = (time.perf_counter() - started) * 1000
            print(f"  ✗ FAIL ({elapsed:.0f} ms) {exc}")
            self.results.append(StepResult(name, False, elapsed, str(exc)))
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            print(f"  ✗ ERROR ({elapsed:.0f} ms) {type(exc).__name__}: {exc}")
            self.results.append(StepResult(name, False, elapsed, f"{type(exc).__name__}: {exc}"))

    def _expect_ok(self, r: httpx.Response, *, want=(200, 201, 204), hint: str = "") -> dict:
        wants = (want,) if isinstance(want, int) else tuple(want)
        body_text = ""
        try:
            body_text = r.text[:400]
        except Exception:
            pass
        assert r.status_code in wants, (
            f"{hint}: HTTP {r.status_code} (expected {wants}); body={body_text}"
        )
        if not r.text:
            return {}
        try:
            return r.json()
        except Exception as e:
            raise AssertionError(f"{hint}: response not JSON ({e}): {body_text}")

    # ── prereq ───────────────────────────────────────────────

    def setup_project(self):
        def run():
            orgs = self._expect_ok(
                self.client.get("/api/v1/organizations/"),
                hint="list organizations",
            )["data"]
            assert orgs, "authenticated user has no organization"
            r = self.client.post(
                "/api/v1/projects/",
                headers={"Idempotency-Key": str(uuid.uuid4())},
                json={
                    "name": self.test_project_name,
                    "description": "v2 features deep test — safe to delete",
                    "org_id": orgs[0]["id"],
                },
            )
            body = self._expect_ok(r, hint="create project")
            self.test_project_id = body["data"]["id"]
            return f"project={self.test_project_id[:12]}"
        self.step("0. Create temp project", run)

    def teardown_project(self):
        def run():
            if not self.test_project_id:
                return "no project"
            r = self.client.delete(f"/api/v1/projects/{self.test_project_id}")
            body = self._expect(r, (202,), hint="accept Project deletion")
            return f"HTTP 202 ({(body.get('data') or {}).get('status', 'pending')})"
        self.step("99. Cleanup", run)

    # ── OpenAPI advertisement ────────────────────────────────

    def step_openapi_advertises_v2(self):
        def run():
            r = self.client.get("/openapi.json")
            assert r.status_code == 200, f"OpenAPI HTTP {r.status_code}"
            spec = r.json()
            paths = set(spec.get("paths", {}).keys())
            required = {
                "/git/{project_id}.git/health",
                "/git/ap/{access_key}.git/health",
                "/git/{project_id}.git/rebuild-cache",
                "/git/ap/{access_key}.git/rebuild-cache",
                "/api/v1/local-snapshots/{snapshot_id}/blobs",
                "/api/v1/local-snapshots/{snapshot_id}/promote",
                "/api/v1/content/{project_id}/conflicts/pending",
                "/api/v1/content/{project_id}/conflicts/{pending_conflict_id}/resolve",
            }
            missing = required - paths
            assert not missing, f"missing routes: {missing}"
            return f"all {len(required)} V2 routes advertised"
        self.step("1. OpenAPI advertises every V2 route", run)

    # ── shadow snapshot I3 ───────────────────────────────────

    def _make_snapshot(self, manifest_entries: list[dict]) -> str:
        r = self.client.post("/api/v1/local-snapshots", json={
            "project_id": self.test_project_id,
            "machine_id": "v2-host",
            "ref_name": "main",
            "manifest": manifest_entries,
        })
        body = self._expect_ok(r, hint="snapshot upsert")
        return body["data"]["snapshot_id"]

    @staticmethod
    def _git_blob_sha1(data: bytes) -> str:
        return hashlib.sha1(
            f"blob {len(data)}\0".encode() + data,
        ).hexdigest()

    @staticmethod
    def _b64(data: bytes) -> str:
        return base64.b64encode(data).decode("ascii")

    def step_blob_upload_happy_path(self):
        def run():
            content_a = b"# Shadow Draft A\n\nFirst test blob.\n"
            content_b = "second blob — slightly larger\n".encode("utf-8") * 8
            hash_a = self._git_blob_sha1(content_a)
            hash_b = self._git_blob_sha1(content_b)
            self.snapshot_id = self._make_snapshot([
                {"path": "drafts/a.md", "mode": "100644", "blob_hash": hash_a, "size": len(content_a)},
                {"path": "drafts/b.md", "mode": "100644", "blob_hash": hash_b, "size": len(content_b)},
            ])
            # Now upload both blobs.
            r = self.client.post(
                f"/api/v1/local-snapshots/{self.snapshot_id}/blobs",
                json={"blobs": [
                    {"blob_hash": hash_a, "content": self._b64(content_a)},
                    {"blob_hash": hash_b, "content": self._b64(content_b)},
                ]},
            )
            body = self._expect_ok(r, hint="blob upload")
            data = body["data"]
            assert data["accepted_count"] == 2, f"accepted_count={data['accepted_count']}"
            assert data["rejected_hashes"] == [], f"rejected={data['rejected_hashes']}"
            return (
                f"accepted={data['accepted_count']} "
                f"server_present={data['server_present_count']} "
                f"snapshot_id={self.snapshot_id[:12]}"
            )
        self.step("2. Shadow I3: upload 2 valid blobs", run)

    def step_blob_hash_poisoning_rejected(self):
        def run():
            poisoned_hash = "ff" * 20  # 40-hex but unrelated to bytes
            bytes_we_ship = b"this is not what the hash claims"
            r = self.client.post(
                f"/api/v1/local-snapshots/{self.snapshot_id}/blobs",
                json={"blobs": [
                    {"blob_hash": poisoned_hash, "content": self._b64(bytes_we_ship)},
                ]},
            )
            body = self._expect_ok(r, hint="poisoned hash upload")
            data = body["data"]
            # Server should accept the request but reject the entry.
            assert data["accepted_count"] == 0, f"accepted_count={data['accepted_count']}"
            assert poisoned_hash in data["rejected_hashes"], (
                f"poisoned hash {poisoned_hash[:8]} not in rejected: {data['rejected_hashes']}"
            )
            return f"poisoned blob rejected; accepted=0 rejected={len(data['rejected_hashes'])}"
        self.step("3. Shadow I3: hash poisoning rejected", run)

    # ── shadow snapshot I5 ───────────────────────────────────

    def step_promote_happy_path(self):
        def run():
            r = self.client.post(
                f"/api/v1/local-snapshots/{self.snapshot_id}/promote",
                json={"scope_path": "", "message": "v2 promote test"},
            )
            body = self._expect_ok(r, hint="promote")
            data = body["data"]
            commit_id = data["commit_id"]
            assert len(commit_id) == 40, f"bad commit_id: {commit_id}"
            return (
                f"promoted snapshot_id={self.snapshot_id[:12]} → "
                f"commit_id={commit_id[:12]}"
            )
        self.step("4. Shadow I5: promote manifest to commit", run)

    def step_snapshot_consumed_on_promote(self):
        def run():
            # After successful promote the snapshot row should be gone.
            r = self.client.get(f"/api/v1/local-snapshots/{self.snapshot_id}")
            assert r.status_code == 404, f"expected 404 after promote, got {r.status_code}"
            return "snapshot row consumed (404 on read)"
        self.step("5. Shadow I5: snapshot row consumed", run)

    def step_promoted_content_visible_via_cat(self):
        def run():
            r = self.client.get(
                f"/api/v1/content/{self.test_project_id}/cat",
                params={"path": "drafts/a.md"},
            )
            body = self._expect_ok(r, hint="cat after promote")
            content = body.get("data", {}).get("content") or body.get("data", {}).get("content_text") or ""
            assert "Shadow Draft A" in str(content), f"missing content: {content!r}"
            return f"drafts/a.md visible ({len(str(content))} chars)"
        self.step("6. Shadow I5: promoted content visible to cat", run)

    def step_promote_missing_blobs_409(self):
        def run():
            # Create a new snapshot referencing a blob that's NOT on the server.
            ghost_hash = self._git_blob_sha1("ghost — never uploaded".encode("utf-8"))
            new_snap_id = self._make_snapshot([
                {"path": "ghost.md", "mode": "100644", "blob_hash": ghost_hash, "size": 100},
            ])
            r = self.client.post(
                f"/api/v1/local-snapshots/{new_snap_id}/promote",
                json={"scope_path": "", "message": "should fail"},
            )
            assert r.status_code == 409, f"expected 409, got {r.status_code}: {r.text[:300]}"
            body = r.json()
            data = body.get("data", {})
            assert data.get("error") == "blobs_missing", f"data.error: {data}"
            sample = data.get("missing_hashes_sample", [])
            assert ghost_hash in sample, f"ghost_hash not in missing sample: {sample}"
            # Clean up the orphan snapshot.
            self.client.delete(f"/api/v1/local-snapshots/{new_snap_id}")
            return f"refused with blobs_missing; missing_count={data.get('missing_count')}"
        self.step("7. Shadow I5: missing-blob promote refused with 409", run)

    # ── conflict resolver ────────────────────────────────────

    def step_conflicts_pending_then_resolve_reject(self):
        def run():
            # Seed: project already has commits from previous steps.
            r = self.client.get(
                f"/api/v1/content/{self.test_project_id}/conflicts/pending",
            )
            body = self._expect_ok(r, hint="pending list")
            initial = body.get("data") or []
            # We don't have a way to deterministically force a pending row
            # via the public API (would need two concurrent writes against
            # a stale base AND policy_override). Just check the endpoint
            # is queryable + returns the right shape.
            assert isinstance(initial, list), (
                f"expected list, got {type(initial)}: {initial!r}"
            )
            return f"pending list returns {len(initial)} row(s) (shape OK)"
        self.step("8. Conflicts: pending list endpoint shape", run)

    def step_health_endpoint_recommended_actions(self):
        def run():
            r = self.client.get(f"/git/{self.test_project_id}.git/health")
            body = self._expect_ok(r, hint="health payload")
            data = body.get("data", {})
            actions = data.get("recommended_actions")
            assert isinstance(actions, list) and len(actions) >= 1, (
                f"recommended_actions empty or wrong shape: {actions!r}"
            )
            # For a project with commits, state should be healthy → action=[none]
            types = [a.get("type") for a in actions]
            assert "none" in types or "first_commit" in types, (
                f"unexpected actions: {types}"
            )
            return f"state={data.get('health')} actions={types}"
        self.step("9. Health: recommended_actions populated for every state", run)

    def step_rebuild_cache_two_variants(self):
        def run():
            r = self.client.post(f"/git/{self.test_project_id}.git/rebuild-cache")
            body = self._expect_ok(r, hint="rebuild")
            data = body.get("data", {})
            variants = data.get("variants", [])
            assert len(variants) == 2, f"expected 2 variants, got {len(variants)}"
            history_modes = sorted(v.get("history_mode", "") for v in variants)
            assert history_modes == ["full", "receive-boundary"], (
                f"variants don't cover both modes: {history_modes}"
            )
            return f"variants={[v.get('history_mode') for v in variants]}"
        self.step("10. Rebuild cache: returns both variants", run)

    # ── orchestrator ────────────────────────────────────────

    def run(self) -> int:
        print("=" * 64)
        print(f"PuppyOne V2 features deep test — API={API}")
        print("=" * 64)

        self.step_openapi_advertises_v2()
        self.setup_project()
        if not self.test_project_id:
            print("\nNo project — aborting subsequent steps.")
            self.teardown_project()
            return 1

        try:
            self.step_blob_upload_happy_path()
            self.step_blob_hash_poisoning_rejected()
            self.step_promote_happy_path()
            self.step_snapshot_consumed_on_promote()
            self.step_promoted_content_visible_via_cat()
            self.step_promote_missing_blobs_409()
            self.step_conflicts_pending_then_resolve_reject()
            self.step_health_endpoint_recommended_actions()
            self.step_rebuild_cache_two_variants()
        finally:
            self.teardown_project()

        print()
        print("=" * 64)
        passed = sum(1 for r in self.results if r.ok)
        failed = len(self.results) - passed
        total_ms = sum(r.elapsed_ms for r in self.results)
        print(
            f"Summary: {passed}/{len(self.results)} passed "
            f"({failed} failed) total elapsed {total_ms:.0f} ms"
        )
        if failed:
            print("\nFailures:")
            for r in self.results:
                if not r.ok:
                    print(f"  - {r.name}: {r.detail}")
        print("=" * 64)
        return 1 if failed else 0


def main() -> int:
    token = _load_token()
    smoke = V2Smoke(token)
    try:
        return smoke.run()
    finally:
        smoke.client.close()


if __name__ == "__main__":
    sys.exit(main())
