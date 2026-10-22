# Workspace opening verification

Date: 2026-10-23. Scope: local source and owned fixtures; no schema change or hosted deployment.

- Required backend gate: `pytest -q -m 'not e2e and not integration and not network'`: **3,555 passed, 955 skipped, 409 deselected**.
- Owned native PostgreSQL/PostgREST and console timing acceptance: **7 passed**. This uses disposable local processes; no hosted database mutation or Docker requirement.
- Supervisor recovery, credential/owner isolation, revocation/replacement and logging cases: **30 passed**.
- Changed-file Ruff, diff whitespace and strict OpenSpec validation passed.
- Desktop `cfb221a4`: scoped single-use handoff, running status fast path and real hook request-budget case. **650 passed, 1 unrelated existing failure** in the broad Cloud suite; the same share-preview failure reproduces on unmodified baseline `06c099aa`. After local WIP integration, **95 relevant cases passed**, production TypeScript and Cloud/workbench boundaries passed. Task-source test TypeScript, lint and full architecture boundaries passed.

## Request budget and security

For an already authorized project and a running workspace, preparation and first display use one status GET and one root-list GET instead of two GET/open-POST/list sequences. This excludes repository-context resolution, watch setup and reset reconciliation. The remaining backend file request still admits current content access, records fenced activity and validates authorization before returning. An explicit running open uses the default workspace already present in the authorization snapshot; automation workspaces retain their exact-row lookup.

The handoff is one-use and expires after five seconds; account/session/API/target/capability mismatch, observed invalidation or request failure rejects reuse. A healthy connection is never an authorization grant. Post-connect open checks reject revoked authority and changed workspace ID, generation, compute epoch, resource or provider binding. Managed-agent descriptors reject unsafe ownership/modes, dead owners, changed credentials/configuration and non-socket/symlink endpoints. Source reload releases borrowed transports without killing the supervisor agent; owner shutdown, credential rotation and agent failure perform owned cleanup/recovery. Uncertain writes remain unreplayed.

## Measurement limits

The earlier observed opening spent 17.34 seconds in connection/readiness within a 29.59-second two-sequence display path; a warm repeated sequence still cost about 4.89 seconds. These are baseline observations, not a new latency guarantee. Stage logs now preserve `workspace_performance` through the production logging bridge; nested durations must not be added twice.

A read-only real Fly comparison was attempted, but admission found the existing workspace no longer running, so the check stopped before connection. It did not wake/allocate a VM or change files. No new warm-latency percentile is claimed. A full supervisor restart still needs initial private-network/SSH establishment, and API reload still needs a fresh SSH file-service handshake. The retained agent removes its unnecessary destruction; it is not a distributed connection service.
