## Implementation and acceptance
- [x] Run the warm revision fence and runtime billing check concurrently before channel use.
- [x] Write a mutation's intent audit concurrently with the warm fence after the cached policy check; close it with `rejected` when admission fails; keep the cold path order.
- [x] Move completion and rejection audits to owned background work drained at shutdown; time both audit inserts.
- [x] (Part B, only if approved) Skip final response authorization for mutation replies; keep it for reads.
- [x] Cover ordering, concurrency, failure and shutdown cases in unit tests; run the workspace and architecture suites.
- [ ] Repeat the local warm-save measurement and record p50/p95 and round-trip counts against the targets.

## Verification (2026-11-14, local)
- `tests/platform/workspace/unit/test_file_write_path.py`: 14 passed (ordering, concurrency, early intent closed as rejected, cold path order, refused caller leaves no row, sandbox refusal, lost transport, stale compute epoch billing recheck, expired reservation, shutdown drain, reads keep final authorization).
- `tests/agent/runtime/test_live_workbench_control.py` against local PostgreSQL: 6 passed; it now waits for the completion audit before reading `audit_logs`.
- `uv run pytest -m 'not e2e and not integration and not network'`: 4166 passed, 10 failed in agent worker dispatch, billing configuration and pub/sub defaults. The same files fail before this change (order and local-environment dependent); none exercise workspace file operations.
- Live runtime selection (`tests/agent/runtime` with `--hosting-live --hosting-s3`): 764 passed, 125 setup errors from `test_supervisor.py`'s `prepared` fixture (`claim` returns no run). The pre-change code shows the same errors.
- `uv run lint-imports`: 7 contracts kept.
- Latency: not yet measured; the running local API predates this change.
