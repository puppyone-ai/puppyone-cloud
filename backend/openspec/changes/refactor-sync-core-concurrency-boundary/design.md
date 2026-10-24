# Design: synchronous core, async edges

## Model

One call chain uses one model. The conversion happens once, at the edge.

| Layer | Rule |
|---|---|
| Repository, `version_engine` data path, storage, data services | Plain `def`; no `asyncio` |
| HTTP routes and dependencies | `def` by default. `async def` only for WebSocket, SSE/streaming, or async-only libraries (LLM streaming, `httpx.AsyncClient`, async SDKs); blocking calls inside go through `await run_blocking(fn, ...)` |
| ARQ jobs | A blocking job is `await run_blocking(sync_job, ...)`. A job that orchestrates async-only clients may stay async, with the same `run_blocking` rule |
| APScheduler jobs (thread pool executor) | Call synchronous functions directly; never create an event loop |
| Process entry points (`main()`, `__main__`) | The only place that may call `asyncio.run` / create a loop |

Async-only adapters (sandbox providers, LLM, MCP transport, WebSocket
notifications) remain async: they are edges, not the data core.

## Boundary helpers (`src/infra/execution/runner.py`)

- `run_blocking(fn, *args, **kwargs)`: runs `fn` in AnyIO's worker threads
  with the default limiter, i.e. the same pool and the same capacity as
  FastAPI `def` routes, sync dependencies and sync streaming iterators.
  Context variables are copied. If the caller is cancelled, the active
  `Execution` (if any) is cancelled so `checkpoint()` stops cooperatively; the
  caller then waits for `fn` to return and the cancellation propagates.
  No deadline is added: long background work keeps its own budget.
- `await_owned(awaitable, on_cancel=None, release=None)`: the same ownership
  rule for an awaitable; `release` disposes a result that arrives after the
  caller was cancelled.
- `run_owned` (repository operations: deadline-bound `Execution`, disconnect
  polling, late-response close) and `run_cleanup` (mandatory release, never
  stopped by an `Execution`) are built on these.

Cancellation always waits for started work. This is the only honest semantics
for threads and matches what lease/pin owners already required; ten local
"shield and drain" copies are removed.

## Capacity

Per process, concurrent blocking calls = `BLOCKING_THREAD_LIMIT` (default 40,
AnyIO's default). Replica capacity is roughly that times `WEB_CONCURRENCY`.
The limit is applied in the API lifespan and every ARQ worker start-up.
The shared boto3 client pool is sized to `BLOCKING_THREAD_LIMIT` plus the
storage I/O pool so threads do not queue for connections.

Nested fan-out never borrows request tokens: Version Engine storage uses one
bounded process-wide pool (`VERSION_STORAGE_IO_THREADS`, default 16) for parallel
object reads/writes. `parallel_each` lets the caller work through the items while
helpers join in and cancels helpers that never started, so neither a request
thread nor a nested pool task ever waits on queued work.

## Timeouts

Threads cannot be stopped, so time limits live in clients: boto3
`connect_timeout=60`, `read_timeout=300`, adaptive retries; PostgREST httpx
timeout 60 s. Long operations stop cooperatively at `checkpoint()` through
`Execution` deadlines/cancellation. Wrapping a thread in a timeout that is then
ignored (the bridge's 1800 s `future.result`) is removed.

## Version Engine storage

The S3 backend's public interface was already synchronous; internally it
bridged into an event loop. It now calls a synchronous S3 client directly.
The bounded byte-credit stream writer, per-operation and per-process I/O
credits, owned joins (never cancel started workers, retrieve every error) and
context propagation are preserved with threads and `threading` primitives.

## Enforcement

- Architecture tests: no event-loop creation outside an explicit entry-point
  list; no `asyncio.to_thread` / `run_in_threadpool` / default-executor
  `run_in_executor` outside the boundary module; no `async def` or `asyncio`
  import in repositories and the Version Engine data path (async edges listed
  explicitly); no `async def` route/dependency without real asynchronous work;
  no inline Supabase `.execute()` in coroutines (existing).
- Ruff `ASYNC` rules (ASYNC230/240 ignored per file where local file I/O is pending).
- Runtime: an event-loop lag monitor (`EVENT_LOOP_LAG_WARN_MS`) logs stalls in
  every environment; staging can additionally set `PYTHONASYNCIODEBUG=1`, which
  logs any callback slower than 100 ms through the intercepted stdlib logger.
