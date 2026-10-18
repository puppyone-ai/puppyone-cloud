## Implementation

- [x] Integrate independent-ref publication with the existing runtime modules.
- [x] Separate provider dispatch from Pi; implement bound Docker/E2B/Fly lifecycle and channel adapters.
- [x] Wire default interactive workspace ownership, isolated automation admission, metering and lifecycle composition.
- [x] Expose authorized native file reads, writes and change invalidation.
- [x] Connect cloud tree/editor/file preview to the live workspace and preserve drafts.
- [x] Verify native private-file sharing and task-only termination in Linux BuildKit.
- [x] Update the canonical runtime architecture and provider configuration documentation.

## Acceptance and merge

- [x] Record backend/frontend/provider-contract and populated PostgreSQL regression results, including pre-existing failures.
- [ ] Run the full populated Docker upgrade rehearsal from the accepted release baseline.
- [ ] Verify the continuous product chain, including authenticated interaction and effective metering.
- [ ] Verify E2B and Fly against actual hosted resources separately.
- [ ] Merge the validated candidate to qubits after required merge gates pass.

Native PostgreSQL and BuildKit regressions are supplementary evidence, not the
full Docker/Supabase upgrade or hosted provider acceptance. Public PTY and running
application preview APIs are not delivered by the native file API.
