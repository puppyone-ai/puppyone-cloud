## Closure
- [x] Document the accepted ownership and file layout.
- [x] Implement durable open operations, fenced execution/recovery and truthful status.
- [x] Enforce common runtime/file readiness for every provider.
- [x] Validate credential/configuration and artifact release consistency.
- [x] Fix Desktop waiting, cancellation and errors without provider knowledge.
- [x] Verify contracts, populated SQL, native files and lifecycle failure scenarios.
- [x] Publish the candidate Fly artifact and pass real product API/Agent/Git/files/watch/resume and I/O acceptance.
- [x] Restore the user's workspace file API with verified matching local services and artifact; retain and hash-check original files.
- [x] Integrate tested changes, update receipts and canonical documentation, preserve unrelated work.
- [ ] Verify actual Desktop file-tree/preview rendering through a real UI interaction.
- [ ] Meet the I/O latency target on the actual local API / hosted database / SIN workspace path.

Real Fly functional acceptance passed in SIN and LAX; LAX also passed the unchanged I/O latency gate. The SIN path via the local LAX egress has a measured 434 ms control/file round trip and does not meet the 300 ms p50 target. The original workspace now opens and serves its unchanged 861 files, with real hosted-database reads at 1.83–1.92 seconds. Receipts preserve all outcomes. Desktop visual acceptance and actual-path performance remain explicit gates.
