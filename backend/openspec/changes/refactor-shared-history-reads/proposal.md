# Change: Share admitted repository reads and History summaries

## Why
History bypasses the existing pinned reader and graph cache: per-object fresh publication readback repeatedly downloads the same pack. Desktop remounts duplicate unfinished work, and list rendering computes every changed tree before showing any commits.

## What Changes
- Keep current authorization and captured refs; reuse immutable pack bytes during reads, while publication retains fresh durable proof.
- Cache immutable graph summaries by project, generation, ref sequence and refs; share builds with subscriber-aware cancellation.
- Add opt-in summary responses and an authorized, reachable-commit detail endpoint; retain existing full response behavior.
- Page location metadata into a disposable disk index; remove the 50,000-object ceiling for this reader without unbounded RAM.
- Coalesce Desktop subscriptions, carry cancellation through Electron and refresh current authority before accepting reused history.

## Approval
The user approved this architecture and systematic implementation in the current session. This records that approval; no additional permission gate is required.

## Impact
Backend read/storage adapters, one additive SQL function, Desktop History/query/IPC ownership. No change to PG transaction authority, S3 object authority, write publication validation or microservice topology.
