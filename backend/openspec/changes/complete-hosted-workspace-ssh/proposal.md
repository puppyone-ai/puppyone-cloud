# Complete Hosted Workspace SSH access

## Why
The accepted Workspace/Adapter boundary is implemented, but the existing SSH
adapter still addresses legacy Scope sandboxes. External agents need native SSH
shells in the same private Hosted Workspace used by the editor.

## Approval and scope
The user requested systematic closure on 2026-10-23 after the prior delivery
explicitly identified the Hosted Workspace SSH/PTY gap. This change implements
that remaining path using the accepted adapter/kernel boundary. It does not
publish a production listener or change the default provider.

## What Changes
- Add short-lived public-key grants for the issuing user's current workspace.
- Add a standard SSH gateway adapter, with no provider SDK or resource ownership.
- Add provider-neutral shell/PTY channels and durable Workspace connection leases.
- Preserve files and occupancy until the exact task's cleanup is acknowledged.
- Validate native SSH with a real local sandbox and populated PostgreSQL upgrade.

## Impact
- Affected spec: workspace-access.
- Affected code: Access SSH adapter, Workspace connections and execution protocol.
- Additive SQL, AsyncSSH dependency and rebuilt runtime image required.
