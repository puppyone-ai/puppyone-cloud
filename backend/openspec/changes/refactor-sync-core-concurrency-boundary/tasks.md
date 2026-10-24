# Tasks

- [x] Boundary helpers, one configured thread capacity, duplicated shield/drain helpers removed.
- [x] Every direct `to_thread` / `run_in_threadpool` routed through `run_blocking`.
- [x] Event loops created only in process entry points (scheduler, agent config, project init, integrity scan).
- [x] Version Engine S3 backend synchronous; bridge loop removed; bounded storage I/O pool.
- [x] Blocking-only routes, dependencies, repositories and Version Engine readers are synchronous.
- [x] Lag monitor, ruff `ASYNC`, architecture tests, AGENTS.md conventions and architecture docs.

Follow-up (not claimed here): service-level coroutines that only await
`run_blocking`, local file I/O behind the ruff per-file ignores, and the owned
Supabase+S3 hosting profile (could not start locally; CI runs it).
