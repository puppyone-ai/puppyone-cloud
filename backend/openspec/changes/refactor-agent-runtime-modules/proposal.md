# Change: Separate Agent runtime capabilities in its existing domain

## Why
The accepted architecture separates Loop, Execution and Files while keeping the
existing Access → Agent ownership and current sandbox deployment.

## What Changes
- Group existing orchestration under `runtime/control` and keep persistence in place.
- Inject `loop`, `execution` and `filesystem` ports with current sandbox adapters.
- Keep Pi conversation details in its adapter and inject native tool capabilities.
- Preserve HTTP, database, checkpoint, billing, task ownership and publication contracts.

## Impact
- Affected spec: agent-chat.
- Source: Agent runtime, sandbox artifact composition, imports and architecture tests.
- Canonical architecture: `puppy-issues:document/agent-runtime.md`.
- No shared-workbench activation, provider switch, external filesystem or editor API is included.
- The user approved this structure and implementation in the current session.
