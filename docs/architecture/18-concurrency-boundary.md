# 18 — Concurrency boundary: synchronous core, async edges

> Status: implemented on `qubits` (OpenSpec `refactor-sync-core-concurrency-boundary`).
> Enforced by `backend/tests/platform/test_concurrency_boundary.py` and ruff `ASYNC`.

## Rule

One call chain uses one model; it switches from async to blocking exactly once,
at the edge. The data clients (supabase-py PostgREST, boto3) and the Version
Engine are synchronous, so the core is synchronous.

| Layer | Rule |
|---|---|
| Repositories, Version Engine, storage, S3Service | Plain `def`; no `asyncio` |
| HTTP routes and FastAPI dependencies | `def` by default (FastAPI runs them in the shared thread pool). `async def` only for real async work: WebSocket, SSE/streaming bodies, async-only clients (LLM streaming, `httpx.AsyncClient`, sandbox SDKs, arq). Blocking calls inside go through `run_blocking` |
| ARQ jobs | Async (arq requires it); blocking steps via `run_blocking` |
| APScheduler jobs | Synchronous jobs on the `threadpool` executor; coroutine jobs only when they need an async client (scheduled sync pull → arq) |
| Process entry points | The only place that may call `asyncio.run` or create a loop |

Interface modules (`*/port.py`, provider bases) stay async where their
implementations are async-only adapters.

## Boundary helpers — `src/infra/execution/runner.py`

| Helper | Use |
|---|---|
| `run_blocking(fn, *args, **kw)` | Any blocking call from async code |
| `run_owned(fn, ..., cancel_when=...)` | Repository reads/writes from HTTP edges: deadline-bound `Execution`, disconnect polling, closes a late result |
| `run_cleanup(fn, *args)` | Mandatory release work; never stopped by an `Execution` |
| `await_owned(awaitable, release=...)` | The same ownership rule for an awaitable |
| `iterate_blocking(iterator)` | Consume a synchronous stream (S3 body) from async code |

Semantics:

- Work runs in AnyIO's worker threads with the default limiter — the same pool
  and limit as `def` routes, sync dependencies and sync `StreamingResponse`
  bodies. Context variables are copied.
- Threads cannot be killed. A cancelled caller cancels the active cooperative
  `Execution` (so `checkpoint()` stops early), waits for the function to
  return, then the cancellation propagates. Leases, pins and request files
  outlive the physical I/O. The caller's cancellation is always the outcome; a
  late failure is logged (an `except Exception` must not swallow cancellation).
- `run_blocking` never invents a deadline; long background work keeps its own.

## Capacity

| Setting | Default | Meaning |
|---|---|---|
| `BLOCKING_THREAD_LIMIT` | 40 | Worker threads per process for `run_blocking` + `def` routes. Replica capacity ≈ this × `WEB_CONCURRENCY` |
| `VERSION_STORAGE_IO_THREADS` | 16 | Process-wide pool for Version Engine object fan-out |
| `EVENT_LOOP_LAG_WARN_MS` | 250 | Log `event_loop_lag` when the loop stalls longer (0 disables) |

Applied by `src/infra/execution/process.py` in the API lifespan, every ARQ
worker start-up and the agent runtime worker. The boto3 connection pool is
sized to `BLOCKING_THREAD_LIMIT + VERSION_STORAGE_IO_THREADS`; physical S3
transfers additionally share 8 process credits and a per-operation window.

## Timeouts and cancellation

Time limits live in clients, not around threads: boto3 `connect_timeout=60`,
`read_timeout=300`, adaptive retries; PostgREST httpx timeout 60 s. Long
operations stop at `checkpoint()` through `Execution` deadlines/cancellation;
a lost `ProjectWriteLease` cancels its owner's `Execution` (threads) or task
(coroutines).

## Version Engine storage

`S3StorageBackend` calls the synchronous `S3Service` directly. Parallel I/O uses
`src/version_engine/storage/io_pool.py`:

- `parallel_each(fn, items, concurrency=n)`: the caller processes items itself
  while pool helpers join in; helpers that never started are cancelled at the
  end and only running ones are awaited, so nested fan-out (batch → chunks)
  cannot deadlock on a bounded pool. A failure stops new items, joins started
  ones, then raises.
- `write_object_stream`: the calling thread is the producer and heartbeat
  (pin renewal at least once a second); batch writes run on the pool under the
  same slot and byte backpressure as before.

## Enforcement

- `test_concurrency_boundary.py`: no `to_thread` / `run_in_threadpool` /
  `run_in_executor` / `anyio.to_thread` outside the runner; event loops only in
  four entry points; blocking-only `async` routes/dependencies rejected (the
  I/O-free `/live` and `/health` probes stay async so they answer when the pool
  is saturated); no `async def`/`asyncio` in repositories and the Version
  Engine except listed async edges.
- `test_async_database_boundary.py`: no inline Supabase `.execute()` in coroutines.
- ruff `ASYNC` (ASYNC230/240 are temporarily ignored per file for the local
  workspace providers, sandbox file transfer and MinerU client, listed in
  `pyproject.toml`).
- Runtime: the lag monitor runs everywhere; staging can also set
  `PYTHONASYNCIODEBUG=1` (every callback slower than 100 ms is logged through
  the intercepted stdlib logger; debug mode has overhead).

## Known remaining work

- ~80 service-level coroutines only await `run_blocking` (e.g. OAuth provider
  `get_connection`, GitHub sync service, deletion/initialization reconcilers).
  They no longer block the loop but could become plain functions with `def`
  routes.
- Local file I/O in the files listed under the ruff per-file ignores.
- `async_context.py` and `cas_backoff.py` are unreferenced and await deletion.
