# Shadow Snapshots — local↔cloud bridge

> Server-side spec for the manifest format the local PuppyOne client
> daemon (or any equivalent integration) pushes to the cloud so the
> server can answer queries about a user's *unpushed* working-tree
> state.

---

## 1. Why

GitHub-style remotes only know about the last `git push`. PuppyOne
needs the cloud to know about the user's *working tree* too —
files that are tracked by `git` but haven't been pushed yet — so that:

- a cloud-side agent can `puppyone fs grep --ref local:<machine>/<branch> X`
  and reach into another teammate's not-yet-pushed work;
- the project dashboard shows real-time progress instead of last-push
  staleness;
- shadow content can be **promoted** into a real commit via the Git
  adapter with one HTTP call (no separate `git push` from the client).

Shadow content is **user-private by default**; no other teammate can
read another user's shadow snapshots without an explicit opt-in.

---

## 2. Storage shape

A snapshot is split across two stores so the row stays cheap to query
while the manifest body has no practical size ceiling:

### 2.1 Supabase row (`local_shadow_snapshots`)

One row per `(project_id, user_id, machine_id, ref_name)` — unique
together. Columns:

| Column | Meaning |
|---|---|
| `id` UUID | Surrogate primary key. Also the S3 path segment for this snapshot's manifest object. |
| `project_id` | The project the snapshot belongs to. RLS bounded. |
| `user_id` | The owner of the snapshot. **Always** the authenticated user; the API rejects spoofing. |
| `machine_id` | Optional client-side hostname / device id so a user can have multiple machines pushing snapshots. |
| `ref_name` | The Git ref (typically `main` or a feature branch) the snapshot represents. |
| `tree_hash` | Optional: SHA-1 of the Git tree the client built locally. Filled when the client computed it; we never trust the client for canonical commits — it's a fast lookup key. |
| `file_count`, `total_bytes` | Pre-computed for dashboards (`GET /local-snapshots` list endpoint, etc.). |
| `created_at`, `updated_at` | Standard timestamps. |

The previous JSONB columns (`manifest`, `previews`, `blob_hashes`)
have been **removed**. They were capped at 8 MiB combined because
larger payloads pressed against Postgres TOAST + JSONB performance,
which broke large-monorepo onboarding. The whole document now lives
in S3 (see §2.2).

The table is service-role-only at the RLS level for V1; user-scoped
reads go through the authenticated API endpoint (§5).

### 2.2 S3 manifest object

S3 key: `shadow-snapshots/{project_id}/{snapshot_id}/manifest.json`

A single JSON document carrying all three logical chunks at once:

```jsonc
{
  "version": 1,
  "snapshot_id": "<uuid>",
  "manifest":     [ { "path": "src/main.py", "mode": "100644", "blob_hash": "...", "size": 4231, "preview": "…" }, ... ],
  "previews":     { "path/to/file.md": "first 200 bytes…", ... },
  "blob_hashes":  [ "<sha1>", "<sha1>", ... ]  // distinct hashes for cheap presence lookups
}
```

One PUT per upsert (overwrite-in-place at the same key), one GET per
read, one DELETE per snapshot delete. The manifest is overwritable
because it's authoritative client-side state — not content-addressed.

Writes are S3-first then DB-upsert, so a transient DB failure leaves
a harmless S3 object that the next successful upsert overwrites, but
never an orphan DB row pointing at a non-existent manifest.

---

## 3. Manifest format (S3 manifest object, `manifest` key)

A JSON array of one object per tracked file:

```jsonc
[
  {
    "path": "src/main.py",        // repo-relative path
    "mode": "100644",             // git file mode
    "blob_hash": "<sha1_hex>",    // SHA-1 over the file's blob
    "size": 4231,                 // bytes
    "mtime": "2026-05-17T12:00Z", // optional client clock
    "ignored": false,             // optional: was filtered by .gitignore but tracked anyway
    "preview": "first 200 bytes…" // optional, see §4
  }
]
```

Constraints the server enforces:

- `path` must be repo-relative (no leading `/`, no `..` segments).
- `mode` must be a known Git file mode (`100644`, `100755`, `120000`,
  `40000`). Submodules (`160000`) are rejected for V1.
- `blob_hash` must be a 40-hex SHA-1. The server does **not** require
  the blob to be present in its object store — see §4.
- `size` must be non-negative and `≤ 50 MiB` per file.

Per-snapshot cap:
- `≤ 100_000` entries (sanity bound — far above any typical project;
  the Linux kernel is ~80k tracked files). A snapshot past this is
  rejected with HTTP 413 + structured detail naming the cap.

The old "manifest JSON byte size ≤ 8 MiB" cap is gone — S3 has no
relevant ceiling for our use, and the per-file + entry-count caps
already bound total upload work.

---

## 4. Object availability

V1 keeps object upload **opt-in and lazy**:

- The client uploads only `manifest` + optional `previews`. Blobs are
  **not** required to be on the server.
- Server-side `puppyone fs grep --ref local:...` runs over `previews`
  when present, and reports "blob unavailable on server" when not.
- Promote-to-commit (§6) refuses unless every referenced blob is in the
  project's object store; the client is expected to push the blobs
  via the Git adapter before calling promote.

Future versions can support eager blob upload via a separate
`POST /api/v1/local-snapshots/{snapshot_id}/blobs` endpoint. The
ingest pipeline is sketched in the I3 stub but not implemented in V1.

---

## 5. API

### `POST /api/v1/local-snapshots`

Upsert a snapshot for the calling user.

Request body:

```jsonc
{
  "project_id": "...",
  "machine_id": "alice-mbp",
  "ref_name": "main",
  "tree_hash": "<optional sha1>",
  "manifest": [ { "path": "...", "mode": "100644", "blob_hash": "...", "size": 123 } ],
  "previews": { "path": "first 200 bytes…" }
}
```

Response:

```jsonc
{
  "snapshot_id": "<uuid>",
  "file_count": 1234,
  "total_bytes": 9876543,
  "updated_at": "2026-05-17T12:00Z",
  "blob_hashes_present_on_server": ["<sha1>"],
  "blob_hashes_missing_on_server": ["<sha1>"]
}
```

Auth: standard JWT for the project owner; the server enforces
`payload.project_id ∈ user.projects`.

### `GET /api/v1/local-snapshots`

List the calling user's snapshots across one or more projects.

### `GET /api/v1/local-snapshots/{snapshot_id}`

Read one snapshot. RLS scopes to the owning user only.

### `DELETE /api/v1/local-snapshots/{snapshot_id}`

Drop a snapshot (e.g. after a real `git push` makes it redundant).

---

## 6. Promote (I5 — not in V1)

Promote a shadow snapshot to a real commit on the project's main
branch:

```
POST /api/v1/local-snapshots/{snapshot_id}/promote
Body: { "scope_path": "...", "message": "..." }
```

This was deliberately deferred from V1: the implementation requires
the engine to ingest the manifest as a `VersionSubmissionIntent` and
push through the normal publish RPC. The plumbing is identical to a
Git receive-pack from a real client; once we have eager blob upload
the promote endpoint becomes a thin orchestrator on top of
`engine.submit_version`.

---

## 7. Open questions tracked here

- **Q1: how deep is the index?** V1 only stores path + size + mime +
  preview text. Full content indexing (the self-hosted text index /
  pg_trgm over content) is deferred to H3.
- **Q2: TTL.** V1 has no TTL — snapshots live until the user deletes
  them or the project is removed. A reaper job is on the H5 roadmap.
- **Q3: cross-machine merge.** Each `(user, machine, ref)` is a
  separate snapshot; the server doesn't try to merge them. The UI
  surfaces them as parallel rows.
