# Verification

Source: `5f1a8b688ceb003ae9719b3f3fa5c102505fee75`, based on local `qubits`
`9a3c19e658de571600fcaa698ab008377b60296d`. Verified 2026-10-23.

## Completed source acceptance

- Required backend regression: **3,548 passed, 953 skipped, 420 deselected**.
  Command: `NOTIFICATIONS_REDIS_URL='' uv run pytest -q -m 'not e2e and not integration and not network'`.
- Final capability/admission, real local SSH/PTY, public HTTP contract and
  architecture checks: **35 passed**. Raw terminal input is delivered without
  a second SSH-library line editor. Unsupported artifacts cannot reserve a
  connection. Slow output cannot block stop acknowledgements.
- Owned PostgreSQL populated upgrade and release contracts: **12 passed**.
  Command: `uv run pytest -q tests/agent/runtime/test_workspace_connections.py tests/agent/runtime/test_migration_release.py`.
  The exact preceding schema plus populated users, workbench and active Agent
  task upgrades, rolls back and reapplies without losing identities or rows.
- Ruff, Node syntax, strict OpenSpec validation and documentation validation
  passed. Documentation retains 25 pre-existing cross-repository link warnings.
- Database artifact lint passed for 14 manifests. Database history policy
  against `9a3c19e6` passed for the one new migration.

## Real Fly acceptance

The [receipt](../../../tests/agent/workbench_acceptance/hosted-ssh-20261023-receipt.json)
records **six passing scenarios** against the pinned linux/amd64 artifact in SIN:
stock OpenSSH binary I/O and exit status; interactive context/resize and live
file notifications; disconnect descendant cleanup; wrong-key rejection and
revocation; expiry; and a new cleanup owner taking over a lost gateway.
All use the same filesystem as the Editor API, before Git publication.

Two complementary invocations cover the final source:

```sh
CLOUD_AGENT_TEST_SSH_PROVIDER=fly CLOUD_AGENT_TEST_FLY=1 uv run pytest -q \
  tests/agent/runtime/test_hosted_ssh_live.py -k 'stock_ssh or new_owner'
CLOUD_AGENT_TEST_SSH_PROVIDER=fly CLOUD_AGENT_TEST_FLY=1 uv run pytest -q \
  tests/agent/runtime/test_hosted_ssh_live.py -k 'not stock_ssh and not new_owner'
```

Both invocations use the same configured app, token, CLI and digest-pinned image;
local acceptance selected the installed native PostgREST binary. The fixture
uses real PostgreSQL authorization and the production SSH adapter on a local
TCP listener. HTTP identity and billing are fixture inputs. This does not test
hosted JWT, billing, model calls, UI interaction or Git publication. E2B is
inactive; offline provider contracts do not establish a new live E2B acceptance.

Earlier attempts recorded TLS EOF/connection failures on the workstation-to-Fly
path; these remain failures in the local diagnostic logs, not skipped passes.
The final six cases passed. Every fixture verified its Machine/Volume cleanup;
a final independent inventory found no Machines using this unique test image,
and the final observed disk was unattached and `pending_destroy`. Existing user
workspaces and their images were unchanged. The test image is retained for replay.

## Database and deployment boundary

A read-only Qubits ledger inspection found **54 applied versions**, ending in
`20261023010000`. Every applied version exists in source; the only pending source
migration is this change's `20261023020000`. No historical migration or immutable
artifact changed. The local upgrade proves this additive candidate; no hosted
SQL was applied during acceptance.

Activation requires the migration, a runtime advertising `workspace_shell`, and
an explicitly configured TCP gateway with a persistent host key. `railway.ssh.toml`
and the acceptance README document that service. No public listener, production
SSH grants, deployed workspace image or provider-default switch was performed.
