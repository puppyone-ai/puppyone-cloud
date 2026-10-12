## Implementation
- [x] Record accepted decisions and failing sandbox process/file tests.
- [ ] Separate task process lifetime from shared provider lifetime; verify legacy tests.
- [ ] Add durable workbench identity, attachment and fenced lifecycle with upgrade tests.
- [ ] Route admitted Runs through shared ownership without filesystem rollback.
- [ ] Bind own refs and preserve the native publication authority.
- [ ] Add authorized interaction policy and workbench lifecycle/usage contracts.
- [ ] Verify recovery, cleanup, concurrency and repository regressions.
- [ ] Update canonical documentation and ISSUE-118 with actual evidence and gaps.

## Partial implementation boundary

The shared path is NOT activated. `AgentService` rejects an explicit workbench
selector when no shared admission adapter is installed; the production
composition root still has no such adapter. Legacy omitted/null selectors keep
the original request digest and Session execution path.

- The Expand migration adds independent identity, immutable Session/Run links,
  resource intent/generation/version/operation fences and a separate worker
  claim contract. Existing resources and cleanup receipts are not adopted.
- The task Expand migration durably allocates non-reused UIDs, records exact
  cleanup identities before provider commands, fences predecessor cleanup and
  blocks idle lifecycle claims while tasks/Runs occupy the workbench. Populated
  upgrade/rollback preserves retained workbench and legacy cleanup resources.
- Shared task control uses private UIDs/homes, denies resource-level recovery,
  resume, pause and deletion, and protects loopback model/Git capabilities.
  Docker task attachment refuses paused resources; E2B tasks require an existing
  lifecycle-owned connection. Task-control lease expiry/duplicate cleanup stop
  only that task. Trusted Git runs under the same task UID before model start or
  after quiescence, so cleanup cannot orphan task Git under a shared service UID.
  The UID allocator, shared supervisor and resource manager are not yet connected.
- BuildKit acceptance runs real concurrent Pi processes, detached descendants,
  PTY and HTTP preview with deterministic model frames and no external network.
  This is not Docker adapter pause/resume or E2B acceptance.
- A separate, unconnected SharedRecovery helper preserves conversation/tool
  checkpoints without filesystem snapshots and retains an original Run-bound
  publication candidate on retry. It is not production takeover integration.
- Run takeover/conversation recovery integration, live branch/ref publication,
  authenticated interaction, activity leases, shared compute metering and
  reconstruction remain unimplemented. The existing Session Git helper is not a completed shared-ref
  publication contract.
- The parallel tool-recovery branch still requires integration and revalidation.
  No A1–A6 item is complete merely because these foundation tests pass.

Reproduction: from `backend/`, build `agent-worker` as
`puppyone-cloud-agent:issue-118-process-tests`, then build
`agent-worker/WorkbenchAcceptance.Dockerfile` with `--no-cache` and the same
context. Run `test_workbench_ownership.py`, `test_workbench_attachment.py`,
`test_workbench_task_leases.py` and `test_migration_release.py` through the owned
PostgreSQL fixture; `test_shared_task_adapter.py` and `test_shared_recovery.py`
cover the currently unconnected Python task/recovery boundaries. The full
Docker adapter suite is `test_workbench_task_processes.py`; it must also pass
before provider acceptance. Canonical issue evidence records command results
and environmental limitations separately.

