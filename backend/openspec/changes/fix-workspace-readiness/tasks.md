## Closure
- [x] Document the accepted ownership and file layout.
- [x] Implement durable open operations, fenced execution/recovery and truthful status.
- [x] Enforce common runtime/file readiness for every provider.
- [x] Validate credential/configuration and artifact release consistency.
- [x] Fix Desktop waiting, cancellation and errors without provider knowledge.
- [x] Verify contracts, populated SQL, native files and lifecycle failure scenarios.
- [x] Publish the candidate Fly artifact and pass real product API/Agent/Git/files/watch/resume and I/O acceptance.
- [ ] Restore the user's hosted preview with verified matching local services and artifact.
- [ ] Integrate tested changes, update receipts and canonical documentation, preserve unrelated work.

Real Fly functional acceptance passed in SIN and LAX; LAX also passed the unchanged I/O latency gate. The SIN path via the local LAX egress has a measured 434 ms control/file round trip and does not meet the 300 ms p50 target. Receipts preserve both outcomes. Existing user preview recovery is a separate release gate.
