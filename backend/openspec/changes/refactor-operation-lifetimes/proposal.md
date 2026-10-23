# Change: Share process resources and operation read lifetimes

## Why
Repeated engine construction discards reusable storage resources. Composed reads can open multiple snapshots, mix revisions and block the async server.

## What Changes
- Inject the process Version Engine into request services; keep grants request-bound.
- Add a file-operation read session and run synchronous reads off the event loop.
- Move owned execution into shared infrastructure; bound interactive read admission and join cancelled workers.
- Preserve workspace/SSH, publication and durable task ownership.

## Impact
- Affected spec: project-structure.
- No schema, URL or payload migration. No new deployment role.
- Approved by the user on 2026-10-23: document the agreed architecture, implement systematically and pass existing tests without pausing.
