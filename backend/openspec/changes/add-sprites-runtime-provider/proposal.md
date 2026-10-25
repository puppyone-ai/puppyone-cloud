# Change: Add Sprites to the existing sandbox provider boundary

## Why
The user selected Fly.io Sprites for persistent, intermittently used workspaces
and explicitly authorized implementation in an isolated Git worktree.

## What Changes
- Register `sprites` alongside Fly Machines, E2B and Docker.
- Adapt REST resource management and WebSocket exec to RuntimeProvider.
- Install an immutable worker bundle and reuse the native file service, Agent,
  shell, authorization and workspace lifecycle contracts.
- Translate lifecycle ownership to expiring Sprites task holds. Confirm warm or
  cold state before acknowledging pause; never substitute deletion for pause.
- Make Sprites the default for new workspaces and retain Fly for existing
  bindings via an explicit provider list. API admission, Agent capability checks,
  open dispatch and maintenance serve those bindings without moving their files.
- Hosted acceptance precedes merging into `qubits`; no backend deployment is
  implied by a local Git merge.

## Impact
- Affected spec: agent-chat.
- Provider configuration, worker packaging, provider implementation and tests.
- No database schema or public file API changes.
- Hosted acceptance requires a separately configured Sprites account. Public API
  contracts and local protocol tests do not establish deployed acceptance.

## Authorization
The user approved adding Sprites within the current sandbox architecture and
requested a new worktree in this session. This records that approved scope.
The follow-up explicitly requests completing validation, making Sprites the
default, and merging into `qubits` only after the integration is verified.

## Read-boundary review
`load_bundle::path.stat` is registered in the operation entrypoint inventory.
It reads a deployment-configured local worker artifact, not project content or
a Version Engine reader. Both preflight and allocation offload the entire bounded
read and SHA-256 verification through `run_blocking`; no user supplies its path.
