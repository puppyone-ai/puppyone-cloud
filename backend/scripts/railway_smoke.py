"""Railway staging smoke runner — hits the real deployed backend.

Reads a user JWT from ``QUBITS_TOKEN`` env (or ``/tmp/jwt.txt``) and
exercises the deployed V2 surface end-to-end:

  1. List projects (auth probe)
  2. Create a temp project
  3. Write file via L4 product write → read back
  4. bulk_write — multi-file atomicity
  5. Move + delete
  6. List + tree + stat
  7. Human control-plane Git-view health endpoint (JWT + repository contract)
  8. Conflict pending list (should be empty for fresh project)
  9. Shadow snapshot upsert (small) + 100,000-entry 413 boundary
 10. Rebuild-cache endpoint (project root)
 11. Cleanup — delete the temp project

Each step prints PASS/FAIL with elapsed time. Failures don't stop the
run; the final summary tells you what broke.

Run:

    QUBITS_TOKEN="<paste>" uv run python scripts/railway_smoke.py

Or with the token in ``/tmp/jwt.txt`` (token body only, no "Bearer "):

    uv run python scripts/railway_smoke.py

Override base URL:

    PUPPYONE_API_URL=https://other-deploy uv run python scripts/railway_smoke.py
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

API = os.environ.get("PUPPYONE_API_URL", "https://qubits-api.puppyone.ai").rstrip("/")
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
REPOSITORY_CONTRACT_HEADERS = {"X-PuppyOne-Repository-Contract": "2"}


def _load_token() -> str:
    token = os.environ.get("QUBITS_TOKEN", "").strip()
    if token:
        return token
    # Search both POSIX and Windows temp dirs so the same script works
    # under WSL / msys2 bash and native cmd / PowerShell.
    import tempfile
    candidates = [
        Path("/tmp/jwt.txt"),
        Path(tempfile.gettempdir()) / "jwt.txt",
    ]
    for p in candidates:
        try:
            if p.exists():
                return p.read_text().strip()
        except OSError:
            continue
    raise SystemExit(
        "No JWT found. Set QUBITS_TOKEN env var or write token to one of: "
        + ", ".join(str(p) for p in candidates),
    )


@dataclass
class StepResult:
    name: str
    ok: bool
    elapsed_ms: float
    detail: str = ""


class Smoke:
    def __init__(self, token: str):
        self.client = httpx.Client(
            base_url=API,
            timeout=TIMEOUT,
            http2=False,  # Railway HTTP/2 has been flaky on this client
            headers={"Authorization": f"Bearer {token}"},
        )
        self.results: list[StepResult] = []
        self.test_project_id: str = ""
        self.test_project_name = f"puppy-smoke-{int(time.time())}"

    # ─── output helpers ──────────────────────────────────────────

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
            self.results.append(
                StepResult(name, False, elapsed, f"{type(exc).__name__}: {exc}"),
            )

    # ─── HTTP helpers ────────────────────────────────────────────

    def _resp_text(self, r: httpx.Response) -> str:
        try:
            return r.json()
        except Exception:
            return r.text[:200]

    def _expect_ok(
        self, r: httpx.Response, *, want=(200, 201, 204), hint: str = "",
    ) -> dict:
        # Default accepts the common 2xx writes (200 read, 201 create,
        # 204 no-content delete). Pass an int or tuple to constrain.
        wants = (want,) if isinstance(want, int) else tuple(want)
        assert r.status_code in wants, (
            f"{hint}: HTTP {r.status_code} (expected {wants}); body={self._resp_text(r)}"
        )
        if not r.text:
            return {}
        try:
            return r.json()
        except Exception as e:
            raise AssertionError(f"{hint}: response not JSON ({e}): {r.text[:200]}")

    # ─── steps ───────────────────────────────────────────────────

    def step_list_projects(self):
        def run():
            r = self.client.get("/api/v1/projects/")
            body = self._expect_ok(r, hint="list projects")
            n = len(body.get("data", []))
            return f"{n} project(s) visible to this JWT"
        self.step("1. List projects (auth probe)", run)

    def step_create_project(self):
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
                    "description": "smoke test — safe to delete",
                    "org_id": orgs[0]["id"],
                },
            )
            body = self._expect_ok(r, hint="create project")
            self.test_project_id = body["data"]["id"]
            return f"created {self.test_project_id} ({self.test_project_name!r})"
        self.step("2. Create temp project", run)

    def step_initial_health(self):
        def run():
            r = self.client.get(
                f"/api/v1/projects/{self.test_project_id}/git-view/health",
                headers=REPOSITORY_CONTRACT_HEADERS,
            )
            body = self._expect_ok(r, hint="project health (empty)")
            data = body.get("data", {})
            state = data.get("health")
            actions = [a.get("type") for a in data.get("recommended_actions", [])]
            assert state in {"empty", "healthy"}, f"unexpected health={state!r}"
            return f"state={state}, recommended_actions={actions}"
        self.step("3. Project root health (initial)", run)

    def step_write_file(self):
        def run():
            r = self.client.post(
                f"/api/v1/content/{self.test_project_id}/write",
                json={
                    "path": "docs/hello.md",
                    "content": "# Hello world\n",
                    "node_type": "markdown",
                    "message": "smoke: first write",
                },
            )
            body = self._expect_ok(r, hint="write_file")
            return f"commit_id={body.get('data', {}).get('commit_id', '')[:12]}"
        self.step("4. Write file (L4 product write)", run)

    def step_read_back(self):
        def run():
            r = self.client.get(
                f"/api/v1/content/{self.test_project_id}/cat",
                params={"path": "docs/hello.md"},
            )
            body = self._expect_ok(r, hint="cat")
            content = body.get("data", {}).get("content")
            content_text = body.get("data", {}).get("content_text")
            got = content or content_text or ""
            assert "Hello world" in str(got), f"missing 'Hello world' in {got!r}"
            return f"read back {len(str(got))} chars"
        self.step("5. Read file back", run)

    def step_bulk_write(self):
        def run():
            r = self.client.post(
                f"/api/v1/content/{self.test_project_id}/bulk-write",
                json={
                    "files": [
                        {"path": "notes/a.md", "content": "A", "node_type": "markdown"},
                        {"path": "notes/b.md", "content": "B", "node_type": "markdown"},
                        {"path": "notes/c.md", "content": "C", "node_type": "markdown"},
                    ],
                    "message": "smoke: bulk-write 3 files",
                },
            )
            body = self._expect_ok(r, hint="bulk_write")
            commit_id = body.get("data", {}).get("commit_id", "")
            assert commit_id, f"bulk_write returned no commit_id: {body}"
            return f"3 files in single commit={commit_id[:12]}"
        self.step("6. bulk_write (3 files, one commit)", run)

    def step_list_tree(self):
        def run():
            r = self.client.get(
                f"/api/v1/content/{self.test_project_id}/tree",
                params={"path": ""},
            )
            body = self._expect_ok(r, hint="tree")
            entries = body.get("data", {}).get("entries", [])
            paths = sorted(e["path"] for e in entries)
            expected = {"docs/hello.md", "notes/a.md", "notes/b.md", "notes/c.md"}
            actual = {p for p in paths if p in expected}
            assert actual == expected, (
                f"missing files in tree; expected {expected}, got {paths}"
            )
            return f"{len(entries)} entries visible at root, including all 4 writes"
        self.step("7. List tree (verify multi-file state)", run)

    def step_move_file(self):
        def run():
            r = self.client.post(
                f"/api/v1/content/{self.test_project_id}/mv",
                json={
                    "old_path": "notes/a.md",
                    "new_path": "notes/renamed-a.md",
                    "message": "smoke: move a.md",
                },
            )
            body = self._expect_ok(r, hint="move")
            commit_id = body.get("data", {}).get("commit_id", "")
            assert commit_id, f"move returned no commit_id: {body}"
            # Verify
            r2 = self.client.get(
                f"/api/v1/content/{self.test_project_id}/stat",
                params={"path": "notes/renamed-a.md"},
            )
            self._expect_ok(r2, hint="stat after move")
            return f"a.md → renamed-a.md (commit={commit_id[:12]})"
        self.step("8. Move file", run)

    def step_delete_file(self):
        def run():
            r = self.client.post(
                f"/api/v1/content/{self.test_project_id}/rm",
                json={"path": "notes/b.md", "message": "smoke: delete b.md"},
            )
            body = self._expect_ok(r, hint="rm")
            commit_id = body.get("data", {}).get("commit_id", "")
            assert commit_id, f"rm returned no commit_id: {body}"
            return f"deleted notes/b.md (commit={commit_id[:12]})"
        self.step("9. Delete file", run)

    def step_health_after_writes(self):
        def run():
            r = self.client.get(
                f"/api/v1/projects/{self.test_project_id}/git-view/health",
                headers=REPOSITORY_CONTRACT_HEADERS,
            )
            body = self._expect_ok(r, hint="health after writes")
            data = body.get("data", {})
            state = data.get("health")
            git_usable = data.get("git_usable")
            actions = [a.get("type") for a in data.get("recommended_actions", [])]
            # Contract: after a successful write sequence the view must
            # remain Git-usable. ``healthy`` is the ideal but
            # ``history_degraded`` is still usable per the spec; only
            # ``current_corrupt`` would be a regression. Empty would
            # mean nothing landed.
            assert state in {"healthy", "history_degraded"}, (
                f"unexpected health={state!r} after writes "
                f"(git_usable={git_usable}); a current_corrupt or empty "
                f"state here is a real bug"
            )
            assert git_usable is True, (
                f"git_usable={git_usable} after writes (state={state}); "
                f"Git transport should still serve clone/fetch"
            )
            return f"state={state} usable={git_usable} actions={actions}"
        self.step("10. Project root health (after writes)", run)

    def step_history(self):
        def run():
            r = self.client.get(
                f"/api/v1/content/{self.test_project_id}/commits",
                params={"limit": 20},
            )
            body = self._expect_ok(r, hint="history")
            commits = body.get("data", {}).get("commits", [])
            # First write + bulk_write + move + delete = ≥ 4 user-visible
            # commits (projection commits are hidden by the history filter).
            assert len(commits) >= 4, (
                f"expected ≥4 commits in history, got {len(commits)}"
            )
            authors = sorted({c.get("who", "") for c in commits})
            return f"{len(commits)} user-visible commits; authors={authors[:3]}"
        self.step("11. Commit history (4+ commits visible)", run)

    def step_conflicts_pending_empty(self):
        def run():
            r = self.client.get(
                f"/api/v1/content/{self.test_project_id}/conflicts/pending",
            )
            body = self._expect_ok(r, hint="pending conflicts")
            data = body.get("data", [])
            assert data == [] or data == {}, f"expected no pending conflicts, got {data}"
            return "no pending conflicts (clean project)"
        self.step("12. Conflict pending list (empty for fresh project)", run)

    def step_shadow_snapshot_small(self):
        def run():
            r = self.client.post(
                "/api/v1/local-snapshots",
                json={
                    "project_id": self.test_project_id,
                    "machine_id": "smoke-host",
                    "ref_name": "main",
                    "manifest": [
                        {
                            "path": "shadow/draft.md",
                            "mode": "100644",
                            "blob_hash": "a" * 40,
                            "size": 23,
                            "preview": "# Draft notes",
                        },
                    ],
                },
            )
            body = self._expect_ok(r, hint="shadow upsert small")
            sid = body.get("data", {}).get("snapshot_id", "")
            assert sid, f"snapshot_id missing: {body}"
            return f"snapshot_id={sid[:12]}, fc={body['data'].get('file_count')}"
        self.step("13. Shadow snapshot upsert (small)", run)

    def step_shadow_snapshot_413(self):
        def run():
            # JSONB size was intentionally retired when manifests moved to
            # S3.  The product contract is now a 100,000-entry guard, which
            # avoids treating a valid large text preview as an invalid
            # snapshot.  Unique paths make this reach the semantic cap rather
            # than a duplicate-path validator.
            manifest = [
                {
                    "path": f"shadow/cap/{index}.txt",
                    "mode": "100644",
                    "blob_hash": "b" * 40,
                    "size": 1,
                }
                for index in range(100_001)
            ]
            r = self.client.post(
                "/api/v1/local-snapshots",
                json={
                    "project_id": self.test_project_id,
                    "machine_id": "smoke-host-413",
                    "ref_name": "main",
                    "manifest": manifest,
                },
            )
            assert r.status_code == 413, (
                f"expected HTTP 413 for oversize manifest, got {r.status_code}; "
                f"body={self._resp_text(r)}"
            )
            # FastAPI's response middleware wraps HTTPException.detail in
            # ApiResponse.data — so the structured limit info lives in
            # body["data"], not body["detail"].
            try:
                detail = r.json().get("data", {})
            except Exception:
                detail = {}
            limit = detail.get("limit") if isinstance(detail, dict) else None
            actual = detail.get("actual") if isinstance(detail, dict) else None
            cap = detail.get("cap") if isinstance(detail, dict) else None
            assert limit, f"413 body missing structured limit field: {self._resp_text(r)}"
            return f"413 limit={limit!r} actual={actual} cap={cap}"
        self.step("14. Shadow snapshot 100k-entry cap → HTTP 413", run)

    def step_shadow_list(self):
        def run():
            r = self.client.get(
                "/api/v1/local-snapshots",
                params={"project_id": self.test_project_id},
            )
            body = self._expect_ok(r, hint="shadow list")
            snapshots = body.get("data", [])
            assert len(snapshots) >= 1, (
                f"expected ≥1 snapshot from the small upsert, got {len(snapshots)}"
            )
            return f"{len(snapshots)} snapshot(s) listed"
        self.step("15. Shadow snapshot list", run)

    def step_rebuild_cache(self):
        def run():
            r = self.client.post(
                f"/api/v1/projects/{self.test_project_id}/git-view/rebuild-cache",
                headers=REPOSITORY_CONTRACT_HEADERS,
            )
            # Read-only members might be refused; allow 200 or 403.
            if r.status_code == 403:
                return "403 (read-only or non-writer on this project)"
            body = self._expect_ok(r, hint="rebuild cache")
            data = body.get("data", {})
            variants = data.get("variants", [])
            assert len(variants) == 2, (
                f"expected 2 cache variants rebuilt, got {len(variants)}"
            )
            heads = [v.get("head", "")[:8] for v in variants]
            return f"rebuilt 2 variants; heads={heads}"
        self.step("16. Rebuild Git view cache (project root)", run)

    def step_cleanup(self):
        def run():
            if not self.test_project_id:
                return "no project to clean up"
            r = self.client.delete(f"/api/v1/projects/{self.test_project_id}")
            if r.status_code == 202:
                status = (r.json().get("data") or {}).get("status", "pending")
                return f"deletion accepted for {self.test_project_id} ({status})"
            raise AssertionError(
                "cleanup must be accepted by the deployment; "
                f"got HTTP {r.status_code}: {self._resp_text(r)}"
            )
        self.step("17. Cleanup — delete temp project", run)

    def stress_move_health_race(self, iterations: int = 12):
        """Hammer the exact write → move → health sequence ``iterations``
        times to surface the bundled-object exists_many race.

        Pre-fix the race (``current_corrupt`` immediately after move,
        self-heals on next commit) was timing-dependent — sometimes 0/3,
        sometimes 1/3 trials. With ``iterations=12`` the empirical hit
        rate climbs to ~30-50%, enough that comparing before-vs-after
        deploy is informative. Post-fix this should be 0/N.
        """
        print(f"\n── STRESS: move → health race, {iterations} iterations ──")
        bug_hits = 0
        for i in range(1, iterations + 1):
            # Each iter: fresh project, single bulk_write, single move,
            # then immediate health check. Delete cleanup at end.
            try:
                orgs = self._expect_ok(
                    self.client.get("/api/v1/organizations/"),
                    hint="list organizations",
                )["data"]
                assert orgs, "authenticated user has no organization"
                r = self.client.post(
                    "/api/v1/projects/",
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                    json={
                        "name": f"stress-{int(time.time())}-{i}",
                        "description": "race stress",
                        "org_id": orgs[0]["id"],
                    },
                )
                pid = r.json()["data"]["id"]
            except Exception as exc:
                print(f"  [iter {i:2d}] project create failed: {exc}")
                continue

            try:
                # bulk_write of 3 files (forces a multi-object bundle)
                self.client.post(
                    f"/api/v1/content/{pid}/bulk-write",
                    json={"files": [
                        {"path": f"x{j}.md", "content": str(j),
                         "node_type": "markdown"}
                        for j in range(3)
                    ]},
                )
                # move (the trigger op)
                self.client.post(
                    f"/api/v1/content/{pid}/mv",
                    json={"old_path": "x0.md", "new_path": "renamed-x0.md"},
                )
                # immediate health check
                h = self.client.get(
                    f"/api/v1/projects/{pid}/git-view/health",
                    headers=REPOSITORY_CONTRACT_HEADERS,
                )
                state = h.json().get("data", {}).get("health")
                git_usable = h.json().get("data", {}).get("git_usable")
                marker = "  ✓" if state in {"healthy", "history_degraded"} else "  ✗"
                if state == "current_corrupt":
                    bug_hits += 1
                print(f"{marker} [iter {i:2d}] state={state:18s} usable={git_usable}")
            finally:
                # Cleanup
                self.client.delete(f"/api/v1/projects/{pid}")

        print(
            f"\n  STRESS RESULT: {bug_hits}/{iterations} hit current_corrupt"
            + ("  ← RACE OPEN" if bug_hits else "  ← race not observed")
        )
        return bug_hits

    # ─── orchestrator ────────────────────────────────────────────

    def run(self) -> int:
        print("=" * 64)
        print(f"PuppyOne Railway smoke — API={API}")
        print("=" * 64)

        self.step_list_projects()
        self.step_create_project()
        if not self.test_project_id:
            print("\nCannot continue without a project — aborting subsequent steps.")
        else:
            self.step_initial_health()
            self.step_write_file()
            self.step_read_back()
            self.step_bulk_write()
            self.step_list_tree()
            self.step_move_file()
            self.step_delete_file()
            self.step_health_after_writes()
            self.step_history()
            self.step_conflicts_pending_empty()
            self.step_shadow_snapshot_small()
            self.step_shadow_snapshot_413()
            self.step_shadow_list()
            self.step_rebuild_cache()
        self.step_cleanup()

        print()
        print("=" * 64)
        passed = sum(1 for r in self.results if r.ok)
        failed = len(self.results) - passed
        total_ms = sum(r.elapsed_ms for r in self.results)
        print(
            f"Summary: {passed}/{len(self.results)} passed  "
            f"({failed} failed)  total elapsed {total_ms:.0f} ms",
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
    smoke = Smoke(token)
    try:
        # CLI: ``--stress N`` skips the standard 17-step sweep and just
        # hammers the move → health race N times. Useful for before/after
        # comparison around the bundled-exists-many fix.
        if "--stress" in sys.argv:
            idx = sys.argv.index("--stress")
            try:
                iters = int(sys.argv[idx + 1])
            except (IndexError, ValueError):
                iters = 12
            print("=" * 64)
            print(f"PuppyOne Railway STRESS — API={API}, iters={iters}")
            print("=" * 64)
            bug_hits = smoke.stress_move_health_race(iters)
            return 1 if bug_hits else 0
        return smoke.run()
    finally:
        smoke.client.close()


if __name__ == "__main__":
    sys.exit(main())
