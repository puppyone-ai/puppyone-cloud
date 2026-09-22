"""Deep test for today's deployed-side changes.

Coverage:
  - PUP-3 backend defense-in-depth: /upload/init + /ap-fs/upload
    block ``.git/`` segments + enforce per-file / per-batch caps.
  - PUP-4 invite flow: /organizations/{id}/invite returns token +
    invite_url + email_sent; /organizations/{id}/invitations relists
    with URLs; DELETE /invitations/{id} revokes; accept returns org
    info; redirect URL roundtrips.
  - Bulk-push regression: a folder containing real-content blobs
    promotes cleanly (would have died on the old raw-bytes-at-dst-key
    path).

Reads ``QUBITS_TOKEN`` + ``PUPPYONE_API_URL`` from env. Runs against
the deployed Railway backend by default.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass

import httpx

API = os.environ.get("PUPPYONE_API_URL", "https://qubits-api.puppyone.ai").rstrip("/")
TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def _load_token() -> str:
    token = os.environ.get("QUBITS_TOKEN", "").strip()
    if not token:
        raise SystemExit("QUBITS_TOKEN not set")
    return token


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@dataclass
class StepResult:
    name: str
    ok: bool
    elapsed_ms: float
    detail: str = ""


class TodaySmoke:
    def __init__(self, token: str):
        self.client = httpx.Client(
            base_url=API,
            timeout=TIMEOUT,
            http2=False,
            headers={"Authorization": f"Bearer {token}"},
        )
        self.results: list[StepResult] = []
        self.project_id: str = ""
        self.project_name = f"today-features-{int(time.time())}"
        self.invitation_id: str = ""
        self.invite_token: str = ""

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

    def _expect(self, r: httpx.Response, want: int | tuple[int, ...], hint: str = "") -> dict:
        wants = want if isinstance(want, tuple) else (want,)
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

    # ── Setup / teardown ─────────────────────────────────────────

    def setup_project(self):
        org_id = self._resolve_org_id()
        r = self.client.post(
            "/api/v1/projects/",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={
                "name": self.project_name,
                "description": "",
                "org_id": org_id,
            },
        )
        body = self._expect(r, (200, 201), hint="create project")
        self.project_id = body["data"]["id"]
        return f"project={self.project_id[:12]}"

    def teardown_project(self):
        if not self.project_id:
            return "no project"
        r = self.client.delete(f"/api/v1/projects/{self.project_id}")
        if r.status_code == 202:
            status = (r.json().get("data") or {}).get("status", "pending")
            return f"HTTP 202 ({status})"
        return f"cleanup HTTP {r.status_code} (non-fatal)"

    # ── PUP-3 backend defense-in-depth ───────────────────────────

    def _extract_policy_detail(self, body: dict) -> dict:
        """The app wraps HTTPException(detail=dict) into the standard
        ``ApiResponse`` envelope: ``{code, message, data: <detail-dict>}``.
        Pull the inner detail from either shape — defensive in case
        FastAPI's default 400 path slips through somewhere."""
        if "data" in body and isinstance(body["data"], dict):
            return body["data"]
        if "detail" in body:
            return body["detail"] if isinstance(body["detail"], dict) else {"message": body["detail"]}
        return body

    def upload_init_blocks_git_segment(self):
        """POST /api/v1/ingest/upload/init with mount_path containing .git
        must 400 with policy_blocked."""
        r = self.client.post(
            "/api/v1/ingest/upload/init",
            json={
                "project_id": self.project_id,
                "files": [
                    {
                        "filename": "config",
                        "parent_path": "imported_repo/.git",
                        "size": 100,
                        "content_type": "application/octet-stream",
                    }
                ],
            },
        )
        assert r.status_code == 400, (
            f"expected 400, got {r.status_code}: {r.text[:300]}"
        )
        detail = self._extract_policy_detail(r.json())
        assert detail.get("code") == "policy_blocked", f"unexpected envelope: {detail}"
        assert detail.get("segment") == ".git", f"got segment={detail.get('segment')!r}"
        return "rejected .git segment with 400"

    def upload_init_blocks_node_modules(self):
        r = self.client.post(
            "/api/v1/ingest/upload/init",
            json={
                "project_id": self.project_id,
                "files": [
                    {
                        "filename": "index.js",
                        "parent_path": "node_modules/lodash",
                        "size": 100,
                        "content_type": "application/octet-stream",
                    }
                ],
            },
        )
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:300]}"
        detail = self._extract_policy_detail(r.json())
        assert detail.get("segment") == "node_modules", f"got {detail}"
        return "rejected node_modules"

    def upload_init_blocks_per_file_size(self):
        """Per-file cap is 100 MB (PUP-3 Q4, raised from 50 MB in #1276).
        A 150 MB file should 400."""
        r = self.client.post(
            "/api/v1/ingest/upload/init",
            json={
                "project_id": self.project_id,
                "files": [
                    {
                        "filename": "big.bin",
                        "parent_path": "data",
                        "size": 150 * 1024 * 1024,
                        "content_type": "application/octet-stream",
                    }
                ],
            },
        )
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:300]}"
        detail = self._extract_policy_detail(r.json())
        msg = json.dumps(detail).lower()
        assert "per-file cap" in msg or "pup-3" in msg, f"got {detail}"
        return "rejected 150MB file"

    def upload_init_accepts_clean_path(self):
        """Sanity: non-blocked paths still go through, get a real
        upload_id back so we don't regress the happy path."""
        r = self.client.post(
            "/api/v1/ingest/upload/init",
            json={
                "project_id": self.project_id,
                "files": [
                    {
                        "filename": "doc.txt",
                        "parent_path": "drafts",
                        "size": 12,
                        "content_type": "text/plain",
                    }
                ],
            },
        )
        body = self._expect(r, 200, hint="happy upload init")
        # /upload/init returns ``UploadInitResponse`` directly (not the
        # ApiResponse envelope) — the files list is at the top level.
        files = body.get("files") or []
        assert files, f"no files in response: {body}"
        first = files[0]
        assert first.get("upload_id"), f"no upload_id: {first}"
        # Best-effort cleanup: abort the multipart we just opened so we
        # don't leak it.
        try:
            self.client.post(
                "/api/v1/ingest/upload/abort",
                json={
                    "task_id": first["task_id"],
                    "s3_key": first["s3_key"],
                    "upload_id": first["upload_id"],
                },
            )
        except Exception:
            pass
        return "happy path returned upload_id"

    # ── PUP-4 invite flow ────────────────────────────────────────

    def _resolve_org_id(self) -> str:
        """Return the caller's primary org id (first in their list)."""
        r = self.client.get("/api/v1/organizations/")
        body = self._expect(r, 200, hint="list orgs")
        orgs = body["data"]
        assert orgs, "user has no orgs"
        return orgs[0]["id"]

    def invite_returns_token_and_url(self):
        """End-to-end happy path: send an invite, expect token + URL in
        the response. If the caller's org has reached its seat limit
        (the most common test-account state — free plan default is 1
        seat, user is their own owner = limit already met), the
        endpoint returns ``HTTP 400`` with ``code=1003`` (FORBIDDEN
        domain code, with AppException's default status_code=400) and
        a "Seat limit reached" message. We accept that outcome as a
        PASS for this step and skip the downstream tests."""
        org_id = self._resolve_org_id()
        self._org_id = org_id
        target = f"pup4-test+{uuid.uuid4().hex[:8]}@example.invalid"
        self._invite_email = target
        r = self.client.post(
            f"/api/v1/organizations/{org_id}/invite",
            json={"email": target, "role": "member"},
        )
        # 200 = success path; 400/403 with a seat-limit message = gate
        # fired. Anything else is a real bug.
        assert r.status_code in (200, 400, 403), (
            f"unexpected HTTP {r.status_code}: {r.text[:300]}"
        )
        body = r.json()
        if r.status_code in (400, 403):
            msg = (body.get("message") or "").lower()
            if "seat limit" in msg:
                return f"seat limit hit (expected on free plan): {body['message']!r}"
            raise AssertionError(f"unexpected {r.status_code} message: {body}")
        data = body["data"]
        assert data.get("token"), f"no token in response: {data}"
        assert data.get("invite_url"), f"no invite_url: {data}"
        assert data["invite_url"].endswith(f"/invite/{data['token']}"), (
            f"invite_url shape unexpected: {data['invite_url']}"
        )
        assert "email_sent" in data, f"missing email_sent: {data}"
        self.invitation_id = data["id"]
        self.invite_token = data["token"]
        return (
            f"invitation_id={data['id'][:8]} token={data['token'][:8]}… "
            f"email_sent={data['email_sent']} "
            f"email_error={data.get('email_error')!r}"
        )

    def _skip_if_no_invitation(self) -> str | None:
        """Returns a SKIP message when the earlier invite step landed on
        the seat-limit 403 instead of creating a row. Used by tests
        that need an existing invitation_id."""
        if not self.invitation_id:
            return "SKIP (no invitation created — seat limit gate fired earlier)"
        return None

    def list_invitations_includes_urls(self):
        skip = self._skip_if_no_invitation()
        if skip:
            return skip
        r = self.client.get(f"/api/v1/organizations/{self._org_id}/invitations")
        body = self._expect(r, 200, hint="list invitations")
        rows = body["data"]
        mine = [row for row in rows if row["id"] == self.invitation_id]
        assert mine, f"my invitation missing from list of {len(rows)}"
        row = mine[0]
        assert row.get("invite_url"), f"list row missing invite_url: {row}"
        assert row.get("token"), f"list row missing token: {row}"
        return f"{len(rows)} pending; mine has invite_url + token"

    def revoke_invitation(self):
        skip = self._skip_if_no_invitation()
        if skip:
            return skip
        r = self.client.delete(
            f"/api/v1/organizations/{self._org_id}/invitations/{self.invitation_id}"
        )
        self._expect(r, 200, hint="revoke")
        # After revoke, the invitation should no longer show up in
        # pending list (status='revoked' is filtered server-side).
        r2 = self.client.get(f"/api/v1/organizations/{self._org_id}/invitations")
        body2 = self._expect(r2, 200, hint="list after revoke")
        still = [row for row in body2["data"] if row["id"] == self.invitation_id]
        assert not still, f"revoked invitation still in pending: {still}"
        return "revoked and absent from pending"

    def revoke_idempotent(self):
        """Second DELETE on the same invitation should return 200, not 404 —
        the service is intentionally idempotent (PUP-4 §6 D8)."""
        skip = self._skip_if_no_invitation()
        if skip:
            return skip
        r = self.client.delete(
            f"/api/v1/organizations/{self._org_id}/invitations/{self.invitation_id}"
        )
        assert r.status_code == 200, f"expected 200 on re-revoke, got {r.status_code}"
        return "second revoke also 200"

    def accept_returns_org_payload(self):
        """Create a fresh invite + accept it as ourselves. The service
        is idempotent for "already a member" so this just confirms the
        accept handler returns the expected org payload shape.

        Self-skips when the seat-limit gate is in effect — we cannot
        create a fresh invite without a free seat, so there's nothing
        to accept."""
        target = f"pup4-self+{uuid.uuid4().hex[:8]}@example.invalid"
        r = self.client.post(
            f"/api/v1/organizations/{self._org_id}/invite",
            json={"email": target, "role": "member"},
        )
        if r.status_code in (400, 403):
            msg = (r.json().get("message") or "").lower()
            if "seat limit" in msg:
                return "SKIP (seat limit gate — accept needs a fresh invite)"
        body = self._expect(r, 200, hint="invite for accept test")
        token = body["data"]["token"]
        inv_id = body["data"]["id"]
        try:
            r2 = self.client.post(
                f"/api/v1/organizations/invitations/{token}/accept",
            )
            body2 = self._expect(r2, 200, hint="accept")
            data = body2["data"]
            required = {"member_id", "org_id", "org_name", "org_slug", "role"}
            missing = required - set(data.keys())
            assert not missing, f"accept response missing keys: {missing}"
            assert data["org_id"] == self._org_id
            return f"got org_name={data['org_name']!r} role={data['role']!r}"
        finally:
            try:
                self.client.delete(
                    f"/api/v1/organizations/{self._org_id}/invitations/{inv_id}"
                )
            except Exception:
                pass

    # ── Project share link (today's MVP, deployed alongside PUP-4) ──

    def project_share_info_returns_token(self):
        """GET /projects/{pid}/share returns a non-empty token for the
        owner. The migration backfills tokens onto every existing row,
        and ``create()`` generates a fresh one for new rows — so any
        project should answer cleanly."""
        r = self.client.get(f"/api/v1/projects/{self.project_id}/share")
        body = self._expect(r, 200, hint="get share info")
        data = body["data"]
        assert data.get("can_share") is True, f"expected can_share=True: {data}"
        token = data.get("share_token") or ""
        assert len(token) >= 16, f"share_token looks bogus: {token!r}"
        self._initial_share_token = token
        return f"got token (len={len(token)}) can_share=True"

    def project_share_rotate_invalidates_old(self):
        """Rotate should replace the token; the new value must differ
        from the previous one (cryptographic regen, not idempotent)."""
        prev = getattr(self, "_initial_share_token", "")
        r = self.client.post(f"/api/v1/projects/{self.project_id}/share/rotate")
        body = self._expect(r, 200, hint="rotate share token")
        new_token = body["data"]["share_token"]
        assert new_token, f"rotate returned empty token: {body}"
        assert new_token != prev, (
            f"rotate didn't change the token (prev={prev[:8]} new={new_token[:8]})"
        )
        self._current_share_token = new_token
        return f"rotated: prev={prev[:8]}… new={new_token[:8]}…"

    def project_share_join_idempotent_for_owner(self):
        """Joining via the current token when you're already the
        project owner is a no-op — the service returns the existing
        membership with ``newly_joined=False``. Verifies the
        idempotency path works."""
        token = getattr(self, "_current_share_token", "")
        if not token:
            return "SKIP (no token captured — earlier rotate failed)"
        r = self.client.post(f"/api/v1/projects/share/{token}/join")
        body = self._expect(r, 200, hint="join via share token")
        data = body["data"]
        assert data["project_id"] == self.project_id
        assert data["newly_joined"] is False, (
            f"owner shouldn't be 'newly joined': {data}"
        )
        assert data["role"], f"role should be set: {data}"
        return (
            f"owner already member; role={data['role']!r} project_name={data['project_name']!r}"
        )

    def project_share_join_rejects_old_token(self):
        """The token captured BEFORE the rotate must now 404. This is
        the revoke-by-rotation semantics — anyone holding the previous
        link sees the same 'invalid' error as someone who never had a
        token, so the link doesn't leak existence after revocation."""
        old = getattr(self, "_initial_share_token", "")
        if not old:
            return "SKIP (no pre-rotate token)"
        r = self.client.post(f"/api/v1/projects/share/{old}/join")
        assert r.status_code == 404, (
            f"expected 404 on rotated-out token, got {r.status_code}: {r.text[:200]}"
        )
        return "rotated-out token correctly returns 404"

    # ── Bulk-push regression (shadow path is a stand-in for full
    #    multipart upload, since the deep test can't easily do the
    #    multipart dance) ────────────────────────────────────────

    def shadow_promote_with_real_content(self):
        """Push a snapshot with two real content blobs, upload them, and
        promote. Same flow as railway_v2_features test 4 but with longer
        content (>500 chars) — the bulk_push fix would have died here
        if a stale raw byte path collided. Verifies it's healthy."""
        content_a = ("# Drafts\n\n" + "x" * 600).encode("utf-8")
        content_b = ("logs\n" * 200).encode("utf-8")
        hash_a = _git_blob_sha1(content_a)
        hash_b = _git_blob_sha1(content_b)
        manifest = [
            {"path": "drafts/big.md", "mode": "100644", "blob_hash": hash_a, "size": len(content_a)},
            {"path": "logs/lots.txt", "mode": "100644", "blob_hash": hash_b, "size": len(content_b)},
        ]
        # Create snapshot.
        snap_r = self.client.post(
            "/api/v1/local-snapshots",
            json={
                "project_id": self.project_id,
                "machine_id": "test",
                "ref_name": "main",
                "manifest": manifest,
            },
        )
        snap_body = self._expect(snap_r, 200, hint="snapshot upsert")
        snap_id = snap_body["data"]["snapshot_id"]
        # Upload blobs.
        ur = self.client.post(
            f"/api/v1/local-snapshots/{snap_id}/blobs",
            json={
                "blobs": [
                    {"blob_hash": hash_a, "content": _b64(content_a)},
                    {"blob_hash": hash_b, "content": _b64(content_b)},
                ]
            },
        )
        ub = self._expect(ur, 200, hint="upload blobs")
        assert ub["data"]["accepted_count"] == 2, f"accepted={ub['data']['accepted_count']}"
        # Promote.
        pr = self.client.post(
            f"/api/v1/local-snapshots/{snap_id}/promote",
            json={"scope_path": "", "message": "today-features deep test"},
        )
        pb = self._expect(pr, 200, hint="promote")
        commit_id = pb["data"]["commit_id"]
        assert commit_id, "no commit_id"
        return f"committed {commit_id[:12]}"

    # ── Driver ───────────────────────────────────────────────────

    def run(self) -> int:
        print("=" * 64)
        print(f"PuppyOne today-features deep test — API={API}")
        print("=" * 64)
        self.step("0. Create temp project", self.setup_project)
        if not self.project_id:
            print("\nNo project — aborting subsequent steps.")
            self.step("99. Cleanup", self.teardown_project)
            return self._summary()

        # PUP-3
        self.step("PUP-3. /upload/init blocks .git/* mount_path", self.upload_init_blocks_git_segment)
        self.step("PUP-3. /upload/init blocks node_modules/*", self.upload_init_blocks_node_modules)
        self.step("PUP-3. /upload/init blocks files >50MB", self.upload_init_blocks_per_file_size)
        self.step("PUP-3. /upload/init happy path still works", self.upload_init_accepts_clean_path)

        # PUP-4
        self.step("PUP-4. /invite returns token + invite_url + email_sent", self.invite_returns_token_and_url)
        self.step("PUP-4. /invitations includes urls on relist", self.list_invitations_includes_urls)
        self.step("PUP-4. DELETE /invitations/{id} revokes", self.revoke_invitation)
        self.step("PUP-4. revoke is idempotent (second call → 200)", self.revoke_idempotent)
        self.step("PUP-4. /invitations/{token}/accept returns org payload", self.accept_returns_org_payload)

        # Project share link (today's MVP)
        self.step("Share. GET /projects/{pid}/share returns token", self.project_share_info_returns_token)
        self.step("Share. POST /share/rotate produces new token", self.project_share_rotate_invalidates_old)
        self.step("Share. Owner join via new token is idempotent", self.project_share_join_idempotent_for_owner)
        self.step("Share. Rotated-out token returns 404", self.project_share_join_rejects_old_token)

        # Bulk push regression (real content path)
        self.step("Regression. Shadow promote with >500-char content", self.shadow_promote_with_real_content)

        self.step("99. Cleanup", self.teardown_project)
        return self._summary()

    def _summary(self) -> int:
        passed = sum(1 for r in self.results if r.ok)
        total = len(self.results)
        elapsed = sum(r.elapsed_ms for r in self.results)
        print()
        print("=" * 64)
        print(f"Summary: {passed}/{total} passed ({total - passed} failed) total elapsed {elapsed:.0f} ms")
        failed = [r for r in self.results if not r.ok]
        if failed:
            print()
            print("Failures:")
            for r in failed:
                print(f"  - {r.name}: {r.detail}")
        print("=" * 64)
        return 0 if not failed else 1


def main() -> int:
    token = _load_token()
    smoke = TodaySmoke(token=token)
    try:
        return smoke.run()
    finally:
        smoke.client.close()


if __name__ == "__main__":
    sys.exit(main())
