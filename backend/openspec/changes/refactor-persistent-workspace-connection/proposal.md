# Change: Persistent workspace file service and connections

## Why
File watches currently start independent SSH transports and file processes exit with their connection. This repeats private-network startup and couples live files to a request channel.

## What Changes
- Supervise a VM-local native file service; attach through a reusable provider transport.
- Share framed requests and watcher subscriptions without holding the connection lock during authorization/audit I/O.
- Preserve workspace ownership, epoch fencing, unknown write outcomes, idle pause, Git publication and Fly-only deployment.
- Recover Desktop event subscriptions with bounded retries and explicit reset.
- Complete the E2B adapter and isolated provider acceptance using the same file service and connection manager. Keep product deployment on Fly with no E2B fallback (user-approved follow-up on 2026-10-22).

## Impact
- Affected specs: agent-chat.
- Affected code: scope_sandbox workbench/control/provider modules, agent-worker file service and Desktop file events.
- No database schema change or Git hosting kernel change.
- Approved by the user on 2026-10-22 after reviewing `puppy-issues/document/agent-runtime.md`; implementation is explicitly authorized in this conversation.
