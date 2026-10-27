# Change: Close workspace readiness and lifecycle recovery

## Why
A new backend attached to an old Fly artifact and accepted initialization before the file service was usable. A stale worker credential then prevented maintenance; Desktop described pausing as starting forever.

## What Changes
- Keep Loop, Execution and Filesystem interfaces and provider boundaries. Extract shared workspace lifecycle coordination.
- Persist idempotent open requests, phases and safe failures; fenced background execution survives HTTP disconnects.
- Admit running only after common runtime capability, file handshake and root-read checks.
- Validate provider credentials before lifecycle work; publish and verify matching backend/worker/artifact configuration.
- Wait for typed lifecycle outcomes in Desktop, with cancellation and a bounded deadline.
- Run common contract, real database/native filesystem and real Fly product acceptance, retaining E2B as an inactive adapter.

## Authorization
The user approved this architecture and its systematic implementation on 2026-10-23, including changing the prelaunch Fly artifacts. No legacy image compatibility is required. Existing project files remain protected.

## Impact
- Workspace service/repository, common control protocol, provider adapters, VM artifact and Desktop opening.
- Additive SQL for durable lifecycle requests and failure reporting. Git hosting authority is unchanged.
