## Closure
- [x] Document the accepted ownership and file layout.
- [x] Implement durable open operations, fenced execution/recovery and truthful status.
- [x] Enforce common runtime/file readiness for every provider.
- [x] Validate credential/configuration and artifact release consistency.
- [x] Fix Desktop waiting, cancellation and errors without provider knowledge.
- [x] Verify contracts, populated SQL, native files and lifecycle failure scenarios.
- [ ] Publish the candidate Fly artifact and pass real product API/Agent/Git/files/watch/resume and I/O acceptance.
- [ ] Restore the user's hosted preview with verified matching local services and artifact.
- [ ] Integrate tested changes, update receipts and canonical documentation, preserve unrelated work.

Local verification is complete. Real Fly artifact upload was rejected by automatic approval review pending explicit authorization for the runtime image and registry.fly.io/puppyone-qubits-workspaces-3ff1fc5c. Do not mark provider acceptance or preview recovery complete until that gate and the real tests pass.
