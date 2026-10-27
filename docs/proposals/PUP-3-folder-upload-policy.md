# PUP-3 — Folder Upload Import Policy

**Status:** ✅ requirements locked, ready for implementation
**Owner:** TBD
**Last updated:** 2026-05-24
**Requirement decisions:** all 8 product questions answered in §2; final contract in §2.5.

This document collects the audit of all current folder/file ingestion paths,
calls out the product gaps, and surfaces the decisions that have to be made
**before** any code lands.

---

## 1. Current state (audit)

Audited paths (web + CLI + backend boundary) all share one property:
**zero product-level filtering**. The only existing cap is a 5000-file
truncation at two layers; everything else (hidden files, VCS internals,
`.gitignore`-tracked files, generated artifacts, symlinks) is uploaded verbatim.

| Entry path | Filters `.git/` | Filters dotfiles | Honors `.gitignore` | File-count cap | Depth cap | Preview / confirm |
|---|---|---|---|---|---|---|
| Web drag-drop ([dropFiles.ts](../../frontend/lib/dropFiles.ts)) | ✗ | ✗ | ✗ | 5000 | ✗ | (dialog list, not exclusion) |
| Web dialog ([FileImportDialog.tsx](../../frontend/components/FileImportDialog.tsx)) | ✗ | ✗ | ✗ | 5000 | ✗ | shows first 5 names, no warning |
| External drop catcher ([useExternalFileDropCatcher.ts](../../frontend/lib/hooks/useExternalFileDropCatcher.ts)) | ✗ | ✗ | ✗ | 5000 | ✗ | ✗ |
| CLI `fs upload -r` (`puppyone-cli: src/commands/fs/lib/transfer-local.js`) | ✗ | ✗ | ✗ | 5000 default | unlimited default | ✗ |
| Backend `/upload/init` ([ingest/router.py](../../backend/src/ingest/router.py)) | ✗ | ✗ | ✗ | n/a | n/a | n/a |
| Backend `/ap-fs/upload` ([access_point_fs.py](../../backend/src/version_engine/entrypoints/http/access_point_fs.py)) | ✗ | ✗ | ✗ | n/a | n/a | n/a |

Concrete evidence (one quote per path):
- `dropFiles.ts:88-154` — `materializeEntry()` recurses on every `FileSystemDirectoryEntry` with no name/pattern check.
- `uploadApi.ts:162-168` — strips leading `./` but does no segment-level filtering; `.git/config` survives.
- `transfer-local.js:34-41` — `entry.isDirectory()` triggers recursion unconditionally; no `.git` / `node_modules` exclusion.
- `validation.py:70-74` — `_FORBIDDEN_SEGMENTS = frozenset({"..", ".", "~"})`. `.git` is allowed.

**Reproduction:** drag any local repo folder → `.git/objects/…/…`, `.git/config`, `.gitignore` all land in the project tree at `<base>/<folder>/.git/…`.

### Why this is a P0-worthy product issue (not just a UX paper cut)

1. **Cost blow-up** — small-looking folder explodes to thousands of files (issue cites 3000+ from a "few hundred" expected). Each file pays an upload round-trip and a Git blob write.
2. **Context pollution** — agent surfaces are seeded with `.git/objects/` binary garbage and `.git/config` (which can include remote URLs + access tokens).
3. **Privacy / security** — `.git/config`, `.env`, `.aws/credentials`, `.ssh/` are all currently uploadable; the issue calls this out explicitly.
4. **Cross-entry inconsistency** — Git push (`/git/...`) follows real Git semantics (only tracked content). Web/CLI folder upload follows "whatever the FS gives me". Same logical action, two answers.

---

## 2. Product decisions — LOCKED

All 8 decisions confirmed 2026-05-24. Each decision is the recommended
default unless otherwise noted; the alternatives considered are in commit
history of this file.

| # | Decision | Locked answer |
|---|---|---|
| Q1 | Default ignore policy | **Hardcoded blocklist + `.gitignore` + `.puppyignore`** layered. Hardcoded minimum: `.git/`, `.DS_Store`, `Thumbs.db`, `node_modules/`, `__pycache__/`, `.venv/`, `.next/`, `dist/`, `build/`. `.gitignore` and `.puppyignore` (new) layered on top for project-specific opt-out. |
| Q2 | Dotfile (`.env`, `.aws/`, …) policy | **Skip by default + "include hidden files" toggle** in the preflight dialog. Count + paths surfaced as "N hidden files skipped" so users have consent. |
| Q3 | Preflight UI on Web | **Threshold-triggered modal.** Small drops (≤ Q4 thresholds) upload immediately like today. Above threshold a summary modal shows "N files / X MB / K skipped (.git/, hidden, …) — confirm / cancel". No tree-view, no per-file checkboxes. |
| Q4 | Hard thresholds | **Per-file: 100 MB** · **Per-batch count: 5000** (matches existing truncation) · **Per-batch total: 1 GB** (new; backend bundle compaction degrades above this). **Preflight modal triggers at 50 files OR 100 MB.** |
| Q5 | CLI vs Web parity | **Identical defaults** + CLI escape hatches: `--include-hidden`, `--no-default-ignores`, `--ignore-file <path>`. CLI must not have surprising silent behavior — it's wired into automation. |
| Q6 | Git push vs folder upload | **Fully decoupled.** Git push (`/git/...`) keeps Git's social contract (only `git add`-ed content). Folder upload (web + CLI) follows the Puppyone import policy defined here. Two distinct user intentions, two distinct contracts. |
| Q7 | Override mechanism | **Per-upload checkboxes in the preflight modal** (`include hidden` / `include .gitignored` / `include default-blocked`) **+ future org-admin lock** preserves the interface for enterprise. No per-project setting (overkill). |
| Q8 | Backend defense-in-depth | **Backend independently enforces the same hardcoded blocklist** at `/upload/init` and `/ap-fs/upload`. Rejects `mount_path` containing any blocklisted segment with 400. Closes regression risk and third-party-client bypass. |

### 2.5 Final product contract

A folder upload (web or CLI) is composed of three filtering stages, applied
in order:

```
1. Hardcoded blocklist     — skip silently, count surfaced in audit
2. .gitignore + .puppyignore — skip silently, count surfaced in audit
3. Dotfile rule            — skip by default; include via toggle/flag
```

If any of the three stages fires and **either** ≥ 50 files **or** ≥ 100 MB
remain after filtering, the web client shows a **preflight modal** with:

- Count + total size of files that will be uploaded.
- Count of files skipped per reason (blocklist / ignored / hidden).
- Three checkboxes (initially unchecked) to include each skipped class.
- Cancel + "Upload N files" buttons.

CLI behavior is the same except no modal — the same summary prints to stdout,
prompting `[y/N]` confirmation when stdin is a TTY. The three CLI flags
(`--include-hidden`, `--no-default-ignores`, `--ignore-file`) compose with
the same stages.

The backend independently re-validates `mount_path` against the hardcoded
blocklist and rejects with 400 on policy violation, regardless of what the
client claimed it filtered.

Git push (`/git/...`) is **untouched** by this policy.

---

## 3. Implementation plan

Implementation will land in 4 PRs to keep blast radius small. Order matters
— later PRs assume earlier landed.

### PR1 — Shared policy module + default blocklist (foundation)
- `backend/src/ingest/policy/upload_policy.py` — single source of truth
  for default blocklist + threshold constants. Exports:
  - `DEFAULT_BLOCKLIST_SEGMENTS: frozenset[str]` (Q1 hardcoded set)
  - `PER_FILE_MAX_BYTES = 100 * 1024 * 1024`
  - `PER_BATCH_MAX_FILES = 5000`
  - `PER_BATCH_MAX_BYTES = 1024 * 1024 * 1024`
  - `PREFLIGHT_FILE_THRESHOLD = 50` / `PREFLIGHT_BYTES_THRESHOLD = 100 * 1024 * 1024`
  - `is_blocked_segment(name: str) -> bool`
- `frontend/lib/uploadPolicy.ts` — mirrors the same constants and exposes
  `applyPolicy(files, options) -> { accepted, skipped: { blocklist, ignored, hidden }, totalBytes }`.
  Includes a small `.gitignore` parser (wildmatch subset; full Git spec out of scope).
- Single shared constant file: `shared/upload-policy.json` (or codegen) so
  TS / Py / JS don't drift. Existing approach in the repo for shared
  constants TBD — check whether `backend/src/common_schemas.py` or a
  similar mechanism is already used; if not, JSON + per-side loader.

### PR2 — Backend defense-in-depth (Q8)
- `backend/src/ingest/router.py::init_upload` — call
  `upload_policy.is_blocked_segment` on every segment of the incoming
  `mount_path`; reject 400 with `{"code": "policy_blocked", "segment": ".git"}`.
- Same check on `backend/src/version_engine/entrypoints/http/access_point_fs.py::upload_*`.
- Enforce Q4 limits: per-file size at `/upload/init`, per-batch count + total
  at `/upload/complete-batch` (sum of `task.metadata.size`).
- Tests: POST `/upload/init` with `mount_path="foo/.git/config"` → 400;
  oversize file → 413; overcount batch → 400.

### PR3 — Frontend filtering + preflight modal (Q1, Q2, Q3, Q7)
- `frontend/lib/dropFiles.ts` — after `materializeEntry`, call
  `applyPolicy(files, { includeHidden, includeIgnored, includeBlocked })`.
- `frontend/components/FileImportDialog.tsx` — when above threshold (Q4
  preflight numbers), render the new preflight section: counts + 3 checkboxes
  + cancel/upload. Pass user-chosen overrides into `applyPolicy`.
- `frontend/lib/hooks/useExternalFileDropCatcher.ts` — same pipeline; falls
  back to the dialog when threshold tripped, otherwise direct upload.
- Tests (Playwright): drop a fixture folder containing `.git/`, assert
  modal shows "9 files skipped (.git/)" and post-upload tree has no `.git/`.

### PR4 — CLI parity (Q5)
- `puppyone-cli: src/commands/fs/lib/transfer-local.js` — port `applyPolicy` to JS
  (or share via the upload-policy package).
- `puppyone-cli: src/commands/fs/commands/upload.js` — new flags:
  - `--include-hidden`
  - `--no-default-ignores`
  - `--ignore-file <path>` (extra rules file)
- Interactive TTY prompt when threshold tripped: print summary, `[y/N]`.
  Non-TTY: skip prompt, log summary, proceed with strict defaults.
- Tests: run against a fixture with `.git/`, assert exit code + summary text.

### Out of scope (deferred)
- Org-admin lock on override (Q7 future leg) — interface preserved, no UI
  shipped. Filed as PUP-3-followup.
- Tree-view modal with per-file checkboxes (Q3 alternative) — explicitly
  rejected; revisit only if user feedback demands it.
- Full Git ignore spec coverage (negation patterns, `**` semantics, etc.)
  — start with wildmatch subset; iterate based on misfires.

### Cross-cutting
- Audit log records `{policy_blocked: N, gitignored: N, hidden: N}` per
  upload batch. Surfaced in the upload-history UI.
- No SQL migrations.
- Frontend test fixture: synthetic folder under `frontend/__tests__/fixtures/repo-with-git/`.

---

## 4. Open questions / out of scope

- Symlink behavior: Web `FileSystemEntry` API follows symlinks transparently (browser-controlled). CLI `readdir` does not. Should we normalize?
- Should backend bulk-write reject the *transaction* if any single path is policy-blocked, or skip and continue? (Recommend: reject — partial upload is worse than no upload for the user's mental model.)
- `.gitignore` parsing semantics — full `gitignore` spec or a subset? (Suggest start with `wildmatch`-style glob; full Git spec is large.)
- How does this interact with future "import from GitHub repo" UI? (That path will use `/git/...` and PUP-3 is by design out of scope.)

---

## 5. Quick reference — all affected files

Frontend:
- [`frontend/lib/dropFiles.ts`](../../frontend/lib/dropFiles.ts)
- [`frontend/lib/uploadApi.ts`](../../frontend/lib/uploadApi.ts)
- [`frontend/lib/hooks/useExternalFileDropCatcher.ts`](../../frontend/lib/hooks/useExternalFileDropCatcher.ts)
- [`frontend/components/FileImportDialog.tsx`](../../frontend/components/FileImportDialog.tsx)
- [`frontend/app/(main)/projects/[projectId]/home/components/GetStartedPanel.tsx`](../../frontend/app/(main)/projects/[projectId]/home/components/GetStartedPanel.tsx)
- [`frontend/app/(main)/projects/[projectId]/data/components/explorer/ExplorerSidebar.tsx`](../../frontend/app/(main)/projects/[projectId]/data/components/explorer/ExplorerSidebar.tsx)
- [`frontend/app/(main)/projects/[projectId]/data/components/explorer/ExplorerTreeRow.tsx`](../../frontend/app/(main)/projects/[projectId]/data/components/explorer/ExplorerTreeRow.tsx)
- [`frontend/app/(main)/projects/components/EmptyWorkspaceState.tsx`](../../frontend/app/(main)/projects/components/EmptyWorkspaceState.tsx)

CLI:
- `puppyone-cli: src/commands/fs/lib/transfer-local.js`
- `puppyone-cli: src/commands/fs/commands/upload.js`

Backend:
- [`backend/src/ingest/router.py`](../../backend/src/ingest/router.py)
- [`backend/src/version_engine/entrypoints/http/access_point_fs.py`](../../backend/src/version_engine/entrypoints/http/access_point_fs.py)
- [`backend/src/version_engine/adapters/git/`](../../backend/src/version_engine/adapters/git/) — Git push path, intentionally NOT in scope per Q6
