# Qubits Railway database deployment gate

This runbook configures and proves the external half of the database release
contract. Repository CI cannot read Railway's **Wait for CI** switch, so a
release is not accepted until an operator records exact-SHA Railway evidence.

The canonical logical inventory is
`backend/deploy/railway-qubits-services.json`. It contains five services:
`api`, `file_worker`, `import_worker`, `sync_worker`, and `mcp_server`. Record
the actual Railway service name and ID for each role in the release receipt;
do not assume the display name equals `SERVICE_ROLE`.

## One-time configuration

For every service in the Qubits Railway environment:

1. Open **Service → Settings → Source**.
2. Confirm the connected repository is `puppyone-ai/puppyone-cloud`.
3. Confirm the source branch is `qubits`.
4. Set **Root Directory** to `backend` (Railway may display `/backend`).
5. Enable **Wait for CI** under GitHub Autodeploys.
6. Open **Variables** and record the service's `SERVICE_ROLE`. The API may omit
   it because `api` is the default; every worker must set its explicit role.
7. Confirm build, pre-deploy, and start settings do not run `supabase db push`.
   Database mutation belongs only to `Deploy Database to Qubits`.
8. Save a receipt row containing environment, Railway service name/ID,
   `SERVICE_ROLE`, repository, branch, root directory, Wait for CI state, UTC
   timestamp, and operator. Do not put credentials in the receipt.

Run the repository-side check before and after dashboard configuration:

```bash
python backend/scripts/check_railway_release_gate.py
```

The check proves inventory and repository configuration. It deliberately does
not claim that the external Railway switch is enabled.

## Failure-blocking rehearsal

Use a harmless test commit whose database workflow is intentionally cancelled
or fails before mutation. Do not introduce invalid schema into a shared
database.

1. Record the full 40-character commit SHA.
2. Open the GitHub Actions run named `Deploy Database to Qubits` and verify its
   `head_sha` equals the recorded SHA and its conclusion is not `success`.
3. For each of the five Railway services, locate the candidate deployment for
   that exact SHA. It must be `SKIPPED` or `BLOCKED`; it must never become the
   active deployment.
4. Record the currently active deployment ID before and after the rehearsal.
   The IDs must match.
5. If any service rolls out, disable Qubits autodeploy for that service,
   restore the previous deployment if needed, correct Wait for CI, and repeat
   the entire rehearsal. One passing service does not cover the others.

## Success-ordering rehearsal

1. Push a reviewed Qubits test commit and record its full SHA.
2. Watch `Deploy Database to Qubits`. Preserve its run URL and completion time.
3. Confirm the workflow completed schema deployment, data lane as applicable,
   schema smoke, drift detection, and attestation for that SHA.
4. For every Railway service, verify its deployment references the same SHA
   and started rollout only after the GitHub workflow succeeded.
5. Verify API `/live` and `/ready`; verify worker process logs show the expected
   `SERVICE_ROLE`; verify the MCP service is healthy through the API readiness
   report. Do not expose internal secrets in captured logs.
6. Search each deployment's build/pre-deploy/start log for `supabase db push`.
   Any occurrence outside the GitHub database workflow fails the rehearsal.

## Retry, recovery, rollback, and emergency boundary

- Retry a failed database workflow for the same SHA only after diagnosing the
  failure. A retry must not be represented as a different commit's evidence.
- If schema deployment succeeded but a later smoke/drift step failed, keep
  application deployment blocked and use an additive forward-fix migration.
  Never edit or delete an applied migration.
- Application rollback may restore the last compatible deployment. Database
  rollback requires the migration-specific reviewed recovery plan; do not run
  ad-hoc reverse SQL from Railway.
- A manual Railway deploy is emergency-only. Record incident ID, approver,
  exact SHA, database attestation, affected services, start/end time, and the
  reason Wait for CI could not be used. Manual deploy never waives schema
  compatibility or post-deploy health checks.

## Minimal release receipt

The receipt must link one GitHub run and all five Railway deployments to one
full SHA. Include the failure rehearsal, success rehearsal, active-deployment
continuity, health results, and reviewer sign-off. Redact tokens, database
URLs, internal domains when required, and all request authorization headers.
