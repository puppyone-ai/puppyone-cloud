# Change: Shorten the live workspace file write path

## Why
Desktop now writes a Cloud document once the person pauses typing, and shows
"saving" while that write is in flight. Each save stayed in that state for
seconds. The local API log (2026-11-13, local API against the hosted database,
8 warm `PUT /workbench/file` samples) shows where the time goes:

| Stage on the response path | p50 |
| --- | --- |
| `files.admission_activity` (SQL revision fence) | 551 ms |
| intent audit insert (not separately timed) | one database round trip |
| `files.operation` (sandbox write) | 250 ms |
| completion audit insert (not separately timed) | one database round trip |
| `files.response_authorization` | 555 ms |

Four sequential database round trips, plus the runtime billing check when
metering is enabled, dominate a 2.4–2.9 s save. The sandbox write is a tenth of
it. A hosted API next to its database pays far less per round trip, but the
count is the same everywhere.

## What Changes
Part A keeps every authorization and audit guarantee:

1. Warm admission runs the `cloud_workbench_activity` revision fence and the
   runtime billing check concurrently. Both still pass before a file channel is
   used.
2. A mutation's intent audit is written concurrently with the warm revision
   fence, only after the cached snapshot has passed the canonical Python policy.
   It still lands before the mutation is sent. If the fence then rejects, a
   `rejected` row closes the intent. Cold admissions keep today's order.
3. Completion and rejection audits leave the response path. They run as owned
   background work, are awaited at shutdown and log failures. An intent without
   completion keeps meaning "unknown outcome"; a reply was never undo.

Part B changes the final-authorization pattern accepted in
`complete-workspace-opening` and needs explicit approval:

4. Mutation replies (`write`, `mkdir`, `move`, `remove`) skip the final response
   re-authorization. They carry no file bytes: a write returns the revision the
   caller derives from its own content, the others return nothing. The mutation
   already happened under fresh admission, so withholding its receipt only turns
   a completed write into an unknown outcome. Reads keep final authorization,
   because they return file bytes.

## Impact
- Code: `backend/src/platform/workspace/service.py` (file operation and admission
  path), its tests. No SQL, API shape, Desktop or sandbox change.
- Warm write response path: four database round trips become two (Part A) or one
  (Part A + B). Reads: unchanged count, billing overlaps the fence.
- Targets in the same local setup: warm save p50 ≤ 1.4 s with Part A, ≤ 0.9 s with
  A + B, measured from the same stage log plus new audit stage timings. Targets
  remain unaccepted until measured.
- No hosted deployment is authorized by this change.

The user approved Part A and Part B in the current conversation on 2026-11-14.
