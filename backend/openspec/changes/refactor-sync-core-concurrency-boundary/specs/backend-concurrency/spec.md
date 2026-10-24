## ADDED Requirements

### Requirement: Single blocking boundary
Async code SHALL call synchronous work only through `run_blocking` (or the
`run_owned` / `run_cleanup` / `await_owned` helpers built on it). The helpers
SHALL share one per-process thread capacity with FastAPI synchronous routes,
configured by `BLOCKING_THREAD_LIMIT`.

#### Scenario: Async route reads the database
- **WHEN** an async route needs a synchronous repository call
- **THEN** it awaits `run_blocking(repository.method, ...)`
- **AND** the event loop thread never executes the database request

#### Scenario: Caller cancelled during blocking work
- **WHEN** the awaiting task is cancelled while the worker thread runs
- **THEN** an active `Execution` is marked cancelled for cooperative checkpoints
- **AND** the caller waits until the function returns before cancellation propagates

### Requirement: Event loops only in entry points
Only process entry points SHALL create or run event loops. Library, request,
scheduler and storage code SHALL NOT call `asyncio.run`, `new_event_loop`,
`run_until_complete` or `run_coroutine_threadsafe`.

#### Scenario: Scheduler job
- **WHEN** an APScheduler job runs in its thread pool
- **THEN** it calls synchronous functions directly without creating a loop

### Requirement: Synchronous data core
Repositories and the Version Engine data path SHALL be synchronous. Routes and
dependencies that perform only blocking work SHALL be plain `def`. Async code
SHALL be limited to edges that need it (WebSocket, streaming, async-only
clients).

#### Scenario: Blocking-only route
- **WHEN** a handler awaits nothing except blocking calls
- **THEN** it is declared `def` and runs in the shared thread pool

### Requirement: Observable event-loop stalls
The API SHALL report event-loop stalls longer than `EVENT_LOOP_LAG_WARN_MS`.

#### Scenario: Inline blocking call regresses
- **WHEN** code blocks the loop for longer than the threshold
- **THEN** a structured `event_loop_lag` warning with the measured delay is logged
