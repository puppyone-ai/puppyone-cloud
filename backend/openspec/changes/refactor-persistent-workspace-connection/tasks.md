## Implementation and acceptance
- [x] Separate shared framing from Pi-specific lifecycle and keep provider commands inside providers.
- [x] Supervise native file service independently of SSH; preserve native FS/CAS and confinement.
- [x] Pool Fly app-private transport; share file requests/watch and fence reconnects.
- [x] Restore Desktop notifications without waking paused compute or replaying writes.
- [x] Pass focused Python, owned database/product-entry, native Linux, Desktop and provider contract regressions.
- [ ] Pass real Fly product API/Pi/Git/files/watch/retention tests; record performance and cleanup.
- [x] Update canonical docs and verification receipts.
- [ ] Integrate the accepted candidate into qubits, preserving unrelated work.

The candidate image is built locally. Automatic approval review rejected upload
to the existing Fly registry pending explicit destination approval. The user
question is pending. No candidate artifact was uploaded and no existing Machine
was upgraded. Local evidence is not real Fly end-to-end or deployment evidence.

Local evidence: `backend/tests/agent/workbench_acceptance/persistent-connection-receipt.json`.
Canonical architecture: `puppy-issues@df29648:document/agent-runtime.md`.
