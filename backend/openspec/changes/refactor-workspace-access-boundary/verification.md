# Workspace / Access boundary verification

Source base: `252dc9eb`, local branch `codex/workspace-access-boundary`.
Verified: 2026-10-23.

## Accepted scope

- Hosted workspace kernel, native files, execution and providers are owned by
  `platform/workspace`; preserved Scope resources live in `workspace/scopes`.
- HTTP routes live in `access/adapters/api`; the existing Scope SSH route and
  connection rendering live in `access/adapters/ssh`.
- Workspace has no direct Access, FastAPI or Starlette imports. Dependency gates
  enforce this and forbid provider SDK/resource-manager imports in the SSH adapter.
- Access composition injects repository transport and credential issuance.
- URLs, SQL identities, VM artifacts, provider selection and existing grants are
  unchanged. No hosted deployment, schema release or resource mutation was run.

## Checks

- Required backend suite: **3484 passed, 953 skipped, 409 deselected**.
  Command: `.venv/bin/python -m pytest -q -m 'not e2e and not integration and not network'`.
  Local loopback HTTP/Redis fixture permissions were required.
- Final runtime/Scope/provider/access suite: **400 passed, 260 deselected**.
- Additional boundary/security subset: **51 passed**.
- New behavior checks cover identical file-conflict HTTP output, SSH delegation
  through the workspace API, cross-Project local completion rejection and native
  text/binary publication with cleanup.
- OpenSpec strict validation passed using cached CLI 1.14.3.
- Ruff undefined-name/syntax checks, full checks on new implementation files, and
  `git diff --check` passed.
- Canonical documentation validator passed: 433 Markdown files, 341 governed.
  Existing repository-wide documentation warnings remain outside this change.

## Limits

This accepts the architectural refactor, not a new public SSH product endpoint.
The existing Scope SSH control API remains subject to its native-Scope and
metering gates. Public SSH into Project workbenches still needs PTY support,
delegated workbench authorization, occupancy/cleanup leases and a gateway.
No new Fly or E2B live-provider acceptance is claimed. Earlier receipts retain
their original source and artifact identities.
