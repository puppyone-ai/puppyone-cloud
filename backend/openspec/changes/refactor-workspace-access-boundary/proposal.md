# Change: Separate Workspace kernel from access adapters

## Why
Hosted lifecycle and provider code live under scope_sandbox while the existing
workspace package owns local files. HTTP entry points and Agent filesystem
implementations obscure the shared kernel's dependency direction.

## What Changes
- Consolidate runtime facilities into platform/workspace.
- Move HTTP routes to adapters/api and existing SSH access to adapters/ssh.
- Keep credential issuance and wire formatting in Access; inject dependencies.
- Share native filesystem contracts without depending on the Agent adapter.
- Preserve URLs, persisted identities, SQL, provider selection and runtime artifacts.

## Approval and scope
The user approved this boundary and requested implementation on 2026-10-23.
This is a behavior-preserving architectural refactor. A public SSH gateway into
Project workbenches, PTY protocol and delegated workbench grants are separate
capabilities; the preserved Scope SSH endpoint must not impersonate them.

## Impact
- project-structure, backend imports, launch scripts, tests and runtime documentation.
- No database migration or hosted resource mutation.
