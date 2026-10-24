# Tasks

- [ ] Boundary helpers, one configured thread capacity, duplicated shield/drain helpers removed.
- [ ] Every direct `to_thread` / `run_in_threadpool` routed through `run_blocking`.
- [ ] Event loops created only in process entry points (scheduler, agent config, project init, integrity scan).
- [ ] Version Engine S3 backend synchronous; bridge loop removed; bounded storage I/O pool.
- [ ] Blocking-only routes, dependencies, repositories and Version Engine readers are synchronous.
- [ ] Lag monitor, ruff `ASYNC`, architecture tests, AGENTS.md conventions and architecture docs.
