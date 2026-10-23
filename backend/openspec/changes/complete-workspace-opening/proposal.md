# Change: Complete workspace opening without redundant connections

## Why
The 2026-10-23 16:00 local opening took 70.07 seconds. A resumed workspace opened a lifecycle SSH channel, a worker file channel and a separate API file channel. Keeping the Fly agent alive did not eliminate these connections. Functional tests did not establish the end-to-end latency goals.

## What Changes
- Hand off provider-started workspaces to the consuming file connection for real readiness. Resume does not create a lifecycle control connection; first initialization still does.
- Validate runtime/workspace/compute identity, read the root once, and publish fenced readiness from that same admitted connection. Keep durable failures/recovery and final authorization.
- Retain authenticated SSH connections and app-scoped credentials inside the Fly provider; keep private networking and provider tokens server-side.
- Reuse authorization snapshots only through the existing SQL revision fence; never treat an in-memory snapshot as permission without a database check.
- Return the prepared root to Desktop, avoiding a second listing. Record separate warm/reconnect/resume timings and counts.

## Impact
Workspace lifecycle, opening coordinator, Fly transport, Desktop preparation, runtime and security tests. Targets are p50 <=3/8/15 seconds and p95 <=5/12/20 seconds for warm/reconnect/resume respectively, excluding first clone. Targets remain unaccepted until measured.

The user approved this scope and these targets in the current conversation on 2026-10-23. Preserve unrelated working changes; no remote application deployment is authorized by this change. Any required SQL uses the existing Qubits migration release workflow.
