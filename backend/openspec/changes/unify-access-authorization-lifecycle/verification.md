# Verification

> Date: 2026-10-23
> Candidate branch: `codex/unified-access-lifecycle`
> Refreshed target: `qubits@4deda12e`
> Scope: local backend acceptance; no hosted migration, deployment or remote push

## Accepted

- 572 tests passed in the combined authority, credential, Git/CLI/SSH component,
  database-release, HTTP, populated-upgrade and Workspace suite. One additional
  real SQL/Python policy-snapshot test passed: 573 distinct tests total.
- PostgreSQL 17 applies the entire current migration chain. The populated upgrade
  additionally applies this candidate over preexisting Projects, credentials,
  Workspaces and active connections, proves transactional rollback, preserves old
  data, and proves legacy admission does not gain a parent grant.
- Native local PostgREST runs the actual authenticated registration -> invitation
  -> application -> approval -> credential-rotation HTTP workflow. No production
  database or user file is used. Responses and invalid requests redact secrets.
- Parallel renewal retries produce one credential and one successor key. Single
  session admission admits one winner. Publication's current-authority locks
  serialize with grant revocation. Revoked, expired, cross-channel and wrong
  Workspace requests fail closed. Registration deletion cannot turn an existing
  managed connection into an unmanaged connection.
- SSH session leases outlive login tickets only while their parent remains valid.
  Revocation and disconnect retain Workspace occupancy until stop acknowledgement;
  durable approval permits reconnect without another approval.
- New issuance is disabled by default during mixed-version rollout. Anonymous
  registration, private-key input, read-only native Shell and protocol confusion
  are denied. Existing human membership/Billing authority stays separate.
- Ruff passes for new lifecycle modules, shared policy, SSH service and new tests.
  OpenSpec strict validation and database migration policy pass.

The SQL/HTTP suite can run independently of Docker:

```bash
# Optional on macOS: point to an installed native PostgREST executable.
PUPPYONE_TEST_POSTGREST_BIN=/path/to/postgrest python -m pytest -q \
  tests/agent/runtime/test_access_lifecycle.py \
  tests/agent/runtime/test_workspace_connections.py \
  tests/agent/runtime/test_workspace_consumer_sql.py
```

## Not accepted as live-provider evidence

The existing Docker SSH acceptance suite plus the new registered-Agent scenario
was attempted. Docker left the disposable VM container in `Created`; `docker
start` did not return. The suite was interrupted and its test container cleaned
up. The earlier Docker PostgREST launcher also timed out; using native PostgREST
completed all SQL/HTTP tests. Unrelated running containers were not restarted.

`tests/agent/runtime/test_hosted_ssh_live.py::test_registered_external_agent_real_shell_and_parent_revocation`
is implemented but its real VM execution is **not passed** in this environment.
No Fly/E2B provider acceptance or hosted readiness is inferred from local results.
The existing test supports an explicitly configured disposable Fly acceptance.

## Activation and limits

Apply these three additive migrations in order, then upgrade every API replica
and SSH gateway before setting `ACCESS_LIFECYCLE_ENABLED=true`:

- `20261023040000_expand_access_authority.sql`
- `20261023041000_expand_access_lifecycle.sql`
- `20261023042000_access_protocol_fences.sql`

Old replicas must be drained before new credentials are issued. Once issuance
has started, rolling back application code requires disabling issuance and
revoking/draining managed credentials and sessions; old code cannot enforce the
new parent or audience contract. Do not roll back additive schema in place.

The backend implements verified-user-owned registrations, optional Organization
binding, temporary/indefinite grants and single-session SSH grants. Delegation
still depends on the approver's current authority. Independent Organization
service-principal delegation, ownership transfer, guest onboarding, invitation
UI and a shipped auto-renewing connector are not implemented here.

Renewal keys expire after 90 days without use; successful rotation renews that
window. A verified owner can reset a key without changing registration identity.
Clients persist the next secret and request ID before renewal and retry exactly
that request after a lost response. A mismatched replay suspends the registration
and invalidates renewal keys and already-issued protocol credentials; owner reset
plus resume is required. Owner key reset also retires earlier protocol proofs.

SSH targets an already-running exact private Workspace. Issuance does not create
or wake a VM. The exchange is a Puppyone connector API, not a complete MCP OAuth
authorization server. Native MCP filesystem resolution remains explicitly 501;
Workspace MCP tools, subdirectory isolation and read-only native Shell remain
unsupported. Existing legacy credentials are not automatically migrated into
long-lived grants.
