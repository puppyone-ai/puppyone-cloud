## Implementation

- [x] Integrate independent-ref publication with the existing runtime modules.
- [x] Separate provider dispatch from Pi; implement bound Docker/E2B/Fly lifecycle and channel adapters.
- [x] Wire default interactive workspace ownership, isolated automation admission, metering and lifecycle composition.
- [x] Expose authorized native file reads, writes and change invalidation.
- [x] Connect cloud tree/editor/file preview to the live workspace and preserve drafts.
- [x] Verify native private-file sharing and task-only termination in Linux BuildKit.
- [x] Update the canonical runtime architecture and provider configuration documentation.
- [x] Package a checksum-pinned flyctl in backend builds; validate Fly credentials independently of unused E2B execution.
- [x] Add an opt-in Fly runtime chain and verify its owned JWT/API/database/object-storage prerequisites.

## Acceptance and merge

- [x] Record backend/frontend/provider-contract and populated PostgreSQL regression results, including pre-existing failures.
- [ ] Run the full populated Docker upgrade rehearsal from the accepted release baseline.
- [ ] Verify the continuous product chain, including authenticated interaction and effective metering.
- [ ] Verify E2B and Fly against actual hosted resources separately.
- [ ] Merge the validated candidate to qubits after required merge gates pass.

On 2026-10-19 the user requested source commit and local qubits integration
before completing the outstanding acceptance. This does not satisfy the
validated-candidate gate above. The later structure-refactor acceptance is
recorded in `../refactor-git-mcp-scope-boundaries/tasks.md`.

Native PostgreSQL and BuildKit regressions are supplementary evidence, not the
full Docker/Supabase upgrade or hosted provider acceptance. Public PTY and running
application preview APIs are not delivered by the native file API.

## Provider boundary and I/O closure

- [x] Update the canonical architecture diagram and provider-only extension contract before implementation.
- [x] Normalize provider connection and preservation results; retain the existing Pi adaptation and keep new supplier branches inside providers.
- [x] Reuse bounded file channels and provider HTTP connections; preserve authorization, idle pause, failure and write-result semantics.
- [x] Add provider extension and channel lifecycle contract tests.
- [x] Measure real Fly file I/O, including native changes, conflicts and pause/reconnect; record cold and warm timings separately.
- [ ] Run the same I/O workload on E2B after explicit approval to upload the candidate worker template.
- [x] Run relevant regressions and record source-bound Fly performance evidence.
