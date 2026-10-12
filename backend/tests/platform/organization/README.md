# Organization and workspace acceptance

This suite owns internal multi-user permission acceptance. It creates only
synthetic Organizations, users, Projects, invitations and Git objects in an
owned PostgreSQL 17 cluster, PostgREST and MinIO. It applies the real immutable
migration chain. It never scans customer Projects or accepts hosted service
URLs. Identity uses locally signed JWTs through the production verifier;
email delivery uses an outbox. Paid billing, SMTP delivery, hosted GoTrue and
paid Agent inference are separate acceptance surfaces.

## Coverage

- `permission_matrix.py` explicitly assigns the minimum role for all 26 named
  Project actions; adding an action without a reviewed expectation fails.
- `integration/test_multi_user_acceptance.py` exercises production HTTP
  routers, services, SQL authority and S3-backed file reads/writes. Covers
  Owner/Admin/Editor/Viewer, private/org visibility, outsiders, invitation
  acceptance, member/role management, self-promotion, creator protection,
  transfer/leave/removal, same-login revocation, Agent admission, token expiry,
  8-way redemption, last-seat concurrency, stale preflight and populated upgrade.
- `integration/test_sql_authorization_contract.py` runs the existing 59 pgTAP
  assertions for database constraints, runtime/human separation, Git credential
  downgrade/revocation and tenant boundaries.
- `integration/test_browser_acceptance.py` uses the production Next build and
  seven isolated Chromium identities against the real owned backend. Workspace
  and invitation requests are never intercepted. It checks exact login return,
  a single content request per open, invitation UI, grant/downgrade/revocation
  with the same session, and no confidential preview for an outsider.
- `tests/repository_hosting/integration/test_read_request_budgets.py` checks
  real database/S3 request counts, bounded batches and permission revocation.
- The existing Agent data-access tests additionally check authority changes at
  renewal, concurrent revocation fencing and bounded history reads.
- Frontend tests cover Strict Mode deduplication, account/token changes, stale
  invitation responses and resource-cache isolation between users.

## Run

Use PostgreSQL 17 on PATH plus pgTAP (`postgresql-17-pgtap` on Ubuntu). Build
`puppyone-entrypoint-minio-build:local` from `docker/Dockerfile.minio` with
`--target build`, and pull `postgrest/postgrest:v14.13`. Install locked backend,
frontend and e2e dependencies. Browser acceptance additionally requires
`npx playwright install chromium` from `e2e`.

```sh
cd frontend
API_INTERNAL_URL=http://127.0.0.1:45461 \
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:45461 \
NEXT_PUBLIC_SUPABASE_ANON_KEY=owned-acceptance-anon-key \
NEXT_PUBLIC_AUTH_EMAIL_ONLY=true PUPPYONE_LOCAL_BUILD=1 \
PUPPYONE_NEXT_DIST_DIR=.next-org-acceptance npm run build
cd ../backend
PUPPYONE_TEST_ORG_BROWSER=1 uv run pytest \
  tests/platform/organization tests/platform/test_project_authorization_policy.py \
  tests/security/test_unified_authorization_architecture.py \
  tests/repository_hosting/integration/test_read_request_budgets.py \
  tests/agent/runtime/test_data_access.py::test_renewal_rejects_changed_authority_in_one_request \
  tests/agent/runtime/test_data_access.py::test_revision_guard_serializes_concurrent_revocation \
  tests/agent/runtime/test_data_access.py::test_history_has_one_query_independent_of_session_count_and_rechecks_access \
  --hosting-live --hosting-s3 -q --junitxml=.organization-test-results/junit.xml
```

The browser fixture owns loopback port 45461 and fails if it is already in use;
all other ports and data directories are allocated per run. Every owned process
is bounded and cleaned up, including failed startup. An unavailable Docker
engine cannot leave acceptance waiting indefinitely. For native macOS services,
set `PUPPYONE_TEST_POSTGREST_BIN` and `PUPPYONE_TEST_MINIO_BIN` to verified local
executables; an optional `PUPPYONE_TEST_PGTAP_SQL` points to a built pgTAP SQL
library instead of installing the extension. `PUPPYONE_TEST_CHROMIUM_BIN` can
select an installed Chromium. These overrides select binaries/libraries, never
existing databases or storage services.

The CI definition is `.github/workflows/organization-workspace-acceptance.yml`.
It uploads JUnit results, browser logs and a revoked-preview screenshot. Passing
this suite is local/CI acceptance, not evidence of a hosted migration/deployment
or a configured GitHub required check. The additive invitation migration must
be applied by the existing database release pipeline before the new API starts.
