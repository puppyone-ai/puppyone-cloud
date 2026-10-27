## Implementation and acceptance
- [x] Separate shared framing from Pi-specific lifecycle and keep provider commands inside providers.
- [x] Supervise native file service independently of SSH; preserve native FS/CAS and confinement.
- [x] Pool Fly app-private transport; share file requests/watch and fence reconnects.
- [x] Restore Desktop notifications without waking paused compute or replaying writes.
- [x] Pass focused Python, owned database/product-entry, native Linux, Desktop and provider contract regressions.
- [ ] Pass real Fly product API/Pi/Git/files/watch/retention tests; record performance and cleanup.
- [x] Update canonical docs and verification receipts.
- [x] Adapt E2B template startup and service transport; prevent read attachment from resuming compute.
- [x] Accept E2B native files, Agent/Git, shared connections, notifications, pause/resume and cleanup using an isolated template; keep deployment inactive.
- [x] Integrate the accepted backend candidate into qubits, preserving unrelated work.

The candidate image is built locally. Automatic approval review rejected upload
to the existing Fly registry pending explicit destination approval. The user
question is pending. No candidate artifact was uploaded and no existing Machine
was upgraded. Local evidence is not real Fly end-to-end or deployment evidence.

Local evidence: `backend/tests/agent/workbench_acceptance/persistent-connection-receipt.json`.
Canonical architecture: `puppy-issues@1d6902d:document/agent-runtime.md`.

E2B follow-up is closed on `33f2415c`: the user authorized its isolated
candidate template, all six real-provider cases passed, and the original I/O
gate passed (50 writes/reads/lists; every p50 <=300 ms and p95 <=1,000 ms).
Final local checks: 215 Agent tests and four native Linux file scenarios passed.
The full backend regression has exactly the same 35 ETL failure/error cases as
untouched `b54cfd53`; no new failure set was introduced. Resource and legacy
snapshot absence are verified. Product configuration remains Fly, with no E2B
key/template activation and no fallback. Evidence:
`backend/tests/agent/workbench_acceptance/e2b-persistent-connection-receipt.json`.

The user explicitly authorized backend source integration on 2026-10-22.
Local `qubits` fast-forwarded from `b54cfd53` to `df46de61`; 215 Agent regressions
passed on the merged checkout. Existing tracked changes, staged state and
untracked file bytes were verified unchanged. No remote push or deployment was
performed. The Desktop companion remains separately on its candidate branch.

The independent Fly artifact upload/acceptance remains open. Source integration
does not establish Fly deployment, full ISSUE-118 A1–A6, billing, browser or PTY
acceptance.
