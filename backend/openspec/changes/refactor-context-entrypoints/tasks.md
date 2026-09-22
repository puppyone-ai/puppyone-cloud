## 1. Schema

- [x] 1.1 Add target tables for upload jobs and upload items.
- [x] 1.2 Keep import jobs canonical and add source identity fields.
- [x] 1.3 Add target tables for durable connections and sync runs.
- [x] 1.4 Add target table for access surfaces.
- [x] 1.5 Add read-only activity aggregation view.
- [x] 1.6 Keep migration additive and leave legacy tables readable.

## 2. Backend Runtime

- [x] 2.1 Route upload creation to `upload_jobs` and `upload_items`.
- [x] 2.2 Route one-shot external imports only through `import_jobs`.
- [x] 2.3 Route durable external source setup through `connections`.
- [x] 2.4 Route every durable source execution through `sync_runs`.
- [x] 2.5 Split ARQ queues into `uploads`, `imports`, and `syncs`. (Done: three queues `etl`/`imports`/`syncs` run as separate worker processes via `SERVICE_ROLE`; the upload queue is named `etl`.)
- [ ] 2.6 Move GitHub webhook sync out of API in-process background tasks.
- [x] 2.7 Stop creating new `import_once` connector or sync bindings.

## 3. Frontend

- [x] 3.1 Present Add content as Upload and Import. (Default + menu now surfaces Upload files + Import from URL + Import from a source.)
- [x] 3.2 Present durable source setup as Connect.
- [x] 3.3 Present Git remote, CLI, Agent, MCP, and Sandbox as Access. (Git remote/CLI/MCP present; Sandbox opened; Agent intentionally OFF per product decision — `AI_AGENT_ENABLED=false`.)
- [x] 3.4 Show upload, import, and sync activity through the aggregation view. (Import + Upload widgets now read `/api/v1/activity` like Sync.)
- [x] 3.5 Hide one-shot imports from Access surfaces.

## 4. Validation

- [ ] 4.1 Add migration tests or Supabase reset coverage for the target schema.
- [ ] 4.2 Add backend tests for ImportJob versus Connection lifecycle.
- [ ] 4.3 Add worker tests for import timeout and cancellation finalization.
- [ ] 4.4 Add frontend tests for the separated product entry points.
