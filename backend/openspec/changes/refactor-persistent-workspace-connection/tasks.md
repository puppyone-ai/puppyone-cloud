## Implementation and acceptance
- [x] Separate shared framing from Pi-specific lifecycle and keep provider commands inside providers.
- [x] Supervise native file service independently of SSH; preserve native FS/CAS and confinement.
- [x] Pool Fly app-private transport; share file requests/watch and fence reconnects.
- [x] Restore Desktop notifications without waking paused compute or replaying writes.
- [x] Pass focused Python, owned database/product-entry, native Linux, Desktop and provider contract regressions.
- [ ] Pass real Fly product API/Pi/Git/files/watch/retention tests; record performance and cleanup.
- [x] Update canonical docs and verification receipts.
- [x] Adapt E2B template startup and service transport; prevent read attachment from resuming compute.
- [ ] Accept E2B native files, Agent/Git, shared connections, notifications, pause/resume and cleanup using an isolated template; keep deployment inactive.
- [ ] Integrate the accepted candidate into qubits, preserving unrelated work.

The candidate image is built locally. Automatic approval review rejected upload
to the existing Fly registry pending explicit destination approval. The user
question is pending. No candidate artifact was uploaded and no existing Machine
was upgraded. Local evidence is not real Fly end-to-end or deployment evidence.

Local evidence: `backend/tests/agent/workbench_acceptance/persistent-connection-receipt.json`.
Canonical architecture: `puppy-issues@df29648:document/agent-runtime.md`.

E2B follow-up: adapter and shared acceptance scenario committed as `71fa3478`
and `d01110a5`. Local regressions pass. Automatic approval review separately
rejected E2B template upload pending explicit destination authorization; no E2B
template/sandbox was created and product configuration remains Fly. Hosted E2B
and latency results are unverified. Evidence:
`backend/tests/agent/workbench_acceptance/e2b-persistent-connection-receipt.json`.
