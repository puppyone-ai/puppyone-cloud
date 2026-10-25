# Verification

> Date: 2026-10-23
> Backend/Web source: `ad5d6fc8`, branch `codex/unified-access-lifecycle`
> CLI source: `puppyone-cli@79c9f79`, branch `codex/unified-access-client`
> Target baseline: `qubits@4deda12e`
> Scope: local regression and disposable real Fly acceptance; no hosted migration, deployment, remote Git push or npm publication

## Accepted

- Required backend regression: **3,617 passed, 953 skipped, 450 deselected**.
  Command: `NOTIFICATIONS_REDIS_URL='' python -m pytest -q -m 'not integration and not network and not e2e'`.
  Local sockets/process inspection required an unsandboxed test invocation;
  restricted-environment failures were retained and not counted as passes.
- Real PostgreSQL 17/PostgREST lifecycle, Workspace connections and populated
  upgrade: **34 passed**. Full current migration chain applies. Approval,
  concurrent renewal, single-session admission, publication/revocation locking,
  deletion fences, parent expiry, request inspection and legacy admission pass.
- Web Workspace regression: **357 tests passed in 47 files**. Includes explicit
  application, approval duration, revocation, late-response identity changes,
  canonical endpoint and navigation ownership checks. TypeScript check passes.
- Standalone CLI `npm test` passes, including **8 new client tests** for private
  persistence, interrupted registration, lost renewal replies, cross-process
  exclusion, recovery, origin binding, secret redaction and stock OpenSSH keys.
- Ruff, strict OpenSpec validation and database artifact/policy checks pass.
- Ego browser fixture verified invitation creation, approval and revocation.
  At 340px viewport width, document and panel have no horizontal overflow;
  button text uses the existing 13px scale. Screenshot capture timed out, so
  no screenshot-based visual acceptance is claimed. The fixture has no live
  account credentials. The isolated browser space was closed.

SQL/HTTP replay from `backend/`:

```bash
PUPPYONE_TEST_POSTGREST_BIN=/path/to/postgrest python -m pytest -q \
  tests/agent/runtime/test_access_lifecycle.py \
  tests/agent/runtime/test_workspace_connections.py \
  tests/agent/runtime/test_workspace_consumer_sql.py
```

## Real Fly acceptance

Both cases passed using the existing SSH-capable artifact recorded in
`backend/tests/agent/workbench_acceptance/hosted-ssh-20261023-receipt.json`:

| Case | Result | Setup | Execution | Cleanup |
| --- | --- | ---: | ---: | ---: |
| Registered external Agent, live Editor file read, parent revocation | Passed | 34.86s | 14.11s | 21.87s |
| Standalone CLI registration → invitation → application → approval → renewal → stock SSH → revoke | Passed | 37.45s | 6.85s | 21.63s |

These are whole scenario timings, not steady-state I/O benchmarks. The second
case executes the CLI checkout's real client/store/connector modules against an
owned HTTP listener, real SQL and the production SSH gateway. Its Shell writes
an uncommitted file; the Editor API reads that same content. Revocation denies
another renewal. The first case additionally proves active-Shell disconnection.
Login identities and billing are fixture inputs. Hosted JWT, deployed gateway,
model calls, billing and Git publication are outside this acceptance.

Replay requires `CLOUD_AGENT_TEST_SSH_PROVIDER=fly`, `CLOUD_AGENT_TEST_FLY=1`, the
usual Fly app/token/CLI settings, and the pinned artifact above. For the CLI
case also set `PUPPYONE_TEST_CLI_ROOT` to the standalone candidate checkout.
Run `tests/agent/runtime/test_hosted_ssh_live.py` with either
`::test_registered_external_agent_real_shell_and_parent_revocation` or
`::test_cli_invitation_approval_and_stock_ssh`.

Failed attempts are not hidden: local Docker stalled before VM start; the
workstation's configured older Fly artifact lacked `workspace_shell` and was
correctly rejected; two CLI-case attempts hit provider SSH login timeouts during
fixture setup. A new image upload was rejected by automatic approval review.
No upload workaround was used: the previously published compatible artifact was
found in the repository receipt and reused unchanged. Final cases passed and
fixtures verified Machine/Volume cleanup. Independent inventory afterward found
zero Machines using the acceptance image and three other Machines untouched.
Existing workspace configuration and the workstation's `.env` were not changed.

## Activation and limits

Apply four additive migrations in order, upgrade every API replica and SSH
gateway, then enable `ACCESS_LIFECYCLE_ENABLED=true`:

- `20261025110000_expand_access_authority.sql`
- `20261025111000_expand_access_lifecycle.sql`
- `20261025112000_access_protocol_fences.sql`
- `20261025113000_access_management_reads.sql`

Drain old replicas before issuing managed credentials. Application rollback
requires disabling issuance and revoking/draining managed credentials/sessions;
old code cannot enforce the new parent/audience contract. Do not roll back the
additive schema in place. Enable a compatible `workspace_shell` runtime for SSH.
Publish the CLI candidate separately; no npm package was released here.

The Web Access panel and recipient portal are implemented; Desktop approval UI,
verified guest onboarding, independent Organization delegation/ownership transfer
and a full MCP OAuth server remain separate work. Native MCP filesystem tools
still return 501. Repository Scope views, path-isolated Bash and read-only native
Shell are not enabled by this authorization change.

SSH targets an already-running exact Workspace. Issuance does not create/wake a
VM. Renewal keys expire after 90 inactive days; protocol proofs last at most an
hour and never outlive their parent. The CLI journals and serializes exchanges;
owner recovery resets an unrecoverable journal/key without changing identity.
Per-connection SSH renewal is automatic; `access credential` exports Git/CLI/MCP
proof on demand into a private file, not a background protocol proxy. Legacy
credentials are not silently converted into long-lived grants.
