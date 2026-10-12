## Implementation
- [x] Add admitted resource reads and bounded directory pages using existing readers.
- [x] Add canonical path codec, authenticated preview and link actions.
- [x] Integrate Desktop workspace and selected-path links.
- [x] Verify URL, permission, snapshot consistency and object-read budgets.
- [x] Verify production Web routes in Chromium and update canonical architecture documents.
- [x] Add PostgreSQL/PostgREST/S3 integration assertions for the resource endpoint.
- [x] Execute the owned-service integration budget test successfully.

## Verification scope

- Backend: 2,788 passed, 885 skipped, 279 deselected with `pytest -m 'not e2e and not integration and not network'`.
- Web: 336 tests passed; production build, ESLint (existing warnings) and dependency boundaries passed.
- Desktop companion: 702 Cloud/Explorer tests passed; source/test types, localization and architecture checks passed. Its dependency checker reports the same 18 pre-existing violations at base `351b2f6e`; this change adds none.
- Production Web + actual FastAPI/native Git reader with isolated identity/control/storage facts: Chromium verified login return, Unicode/reserved names, directory navigation, copy-link, image/unsupported-format preview, lossless byte-path fallback, denied workspace and mobile layout. File/root/directory opens each issue one content API request; no page errors.
- Owned PostgreSQL 17 + PostgREST 14.13 + MinIO integration passed during Organization/workspace acceptance. Native loopback services provide the same real transports when local Docker cannot start. Resource reads met the fixture budgets: file 6 database/3 S3 requests; root directory 5 database/2 S3 requests. This proves request counts, not hosted latency.
- No schema migration, external-guest invitation or hosted deployment is included.

Run the budget gate with the owned services or the Agent/Organization integration jobs:

```sh
cd backend
.venv/bin/python -m pytest -q tests/repository_hosting/integration/test_read_request_budgets.py::test_content_reads_and_same_snapshot_reuse_have_real_budgets --hosting-live --hosting-s3
```
