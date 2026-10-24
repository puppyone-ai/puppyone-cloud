# Synchronous core and one async/blocking boundary

## Why

The data clients are synchronous (supabase-py PostgREST, boto3), and so is the
Version Engine, but call chains switch models repeatedly:

- the version S3 backend submits coroutines to a process-wide bridge event loop
  (`version-s3-loop`), which hands each boto3 call to yet another thread;
- request, scheduler and worker code create event loops inside threads
  (`asyncio.run`, `new_event_loop`) to call async wrappers around sync work;
- ~300 direct `asyncio.to_thread` calls draw from a second, unconfigured pool,
  while about ten modules re-implement "shield and drain on cancellation";
- some `async def` routes, dependencies and repositories run sync I/O inline.

Each switch costs a thread hop, hides capacity, and makes timeouts and
cancellation hard to reason about (the bridge's 1800 s timeout, for example,
waits forever after expiring).

## Approval and scope

The user approved this architecture on 2026-10-24 ("按照这套最佳实践的架构去
优化"): synchronous data core, async only at edges, one boundary helper with one
configured capacity, client-level timeouts, event loops only in process entry
points, and tests/lint that keep it that way. Backend-only; no SQL, API or wire
format changes.

## Changes

- `run_blocking` / `await_owned` in `src/infra/execution/runner.py` become the
  only async→sync boundary. Cancellation joins the worker (threads cannot be
  killed) and signals an active cooperative `Execution`.
- One per-process thread capacity, `BLOCKING_THREAD_LIMIT`, shared with
  FastAPI `def` routes, configured at API and worker start-up; S3 connection
  pools sized to match.
- Replace every direct `asyncio.to_thread` / `run_in_threadpool` and the
  duplicated shield/drain helpers.
- Remove event-loop creation outside process entry points, including the
  Version Engine S3 bridge loop: the storage backend calls boto3 directly and
  fans out through one bounded storage I/O pool.
- Routes and dependencies that only do blocking work become `def`.
  Repositories and the Version Engine data path are synchronous.
- Event-loop lag monitoring, opt-in asyncio debug guidance, ruff `ASYNC` rules
  and architecture tests for every rule above.

## Impact

- Affected specs: new `backend-concurrency`; modified `s3-storage`
  (asynchronous adaptation requirement).
- Affected code: `infra/execution`, `infra/s3`, `version_engine/storage`,
  `version_engine/read`, scheduler jobs, routers/dependencies using blocking
  calls, `main.py` and worker start-up.
- Behaviour: a cancelled caller now always waits for its started blocking call
  to return. Client timeouts (S3 read 300 s, PostgREST 60 s) bound that wait.
