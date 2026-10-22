# Change: Reuse admitted workspace opening and transport

## Why
A running workspace took 29.6 seconds to display: 17.34 seconds establishing a new Fly connection, followed by duplicate client opening and directory requests. Warm opening still repeated four database reads/commands.

## What Changes
- Hand the already authorized root listing from preparation to display, scoped to account, API, project, capabilities and workspace generation.
- Read running workspaces directly; reserve wake requests for stopped resources and explicit retry.
- Reuse the checked activity result, retain final revocation/resource checks, and bypass management connectivity checks when a healthy file connection already exists.
- Keep the isolated local Fly tunnel owned by the local supervisor across API/worker source reloads; rotate on configuration change and clean up on exit.
- Record safe structured stage timings and connection reuse/reconnect reasons.

## Impact
- Desktop cloud files/workbench composition; backend platform/workspace and local launcher.
- No schema or native Git storage change. No deployment/provider switch.
- Approved by the user on 2026-10-23 in this conversation after reviewing the cost breakdown and safety requirements.
