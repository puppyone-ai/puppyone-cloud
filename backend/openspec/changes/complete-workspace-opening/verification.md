# Workspace opening verification

Status: implementation integrated; Qubits database released; local API and worker activated. Remote application deployment and production SLO acceptance are not claimed.
Date: 2026-10-23.

## Ownership and request budget

- `workspace/lifecycle.py`: durable compute/initialization owner. Existing resume does not open a disposable lifecycle or worker file channel. It persists `checking_files`; new allocation still initializes through the native lifecycle protocol.
- `workspace/opening.py`: the API (or internal consumer) proves file hello and root access using the channel it retains, rechecks current human authority and exact runtime identity, then publishes ready through `cloud_workbench_finish_open`. Existing GET status supports older clients and returns optional root entries; it never starts compute without a durable explicit open intent.
- `workspace/admission.py`: bounded snapshots supply policy facts only. `cloud_workbench_activity` validates their current authorization revision before access. Final response authorization stays fresh. Warm file requests use two database roundtrips instead of three; status is one additional roundtrip.
- `execution/providers/fly_ssh.py` and `fly_certificate.py`: official Fly private proxy, persistent authenticated SSH, fresh service binding per file connection. Fly's signed app restriction is validated and preserved byte-for-byte. Limits and idle retention follow existing file-channel settings. A new compute epoch replaces SSH but retains the exact private route. No desktop provider credentials or global authorization cache.
- Desktop consumes the verified root once, coalesces simultaneous root reads, and fences navigation/generation changes. Normal status/list clients remain compatible.

## Tests

- Required backend suite: `python -m pytest -q -m 'not e2e and not integration and not network' --disable-warnings`: **3597 passed, 955 skipped, 431 deselected**. The initial sandbox run could not bind local sockets; the successful run used owned loopback services with the necessary execution permission.
- Final readiness preflight and snapshot tests: **24 passed** after adding the exact RPC activation probe.
- Real native PostgreSQL pending-migration upgrades/readiness/connection tests: **25 passed**. Includes preceding unpublished `20261023020000_hosted_workspace_connections.sql`, then `20261023030000_complete_workspace_file_readiness.sql`; tests preserve existing Machines, Volumes, workspace rows and task occupancy.
- Desktop repository tests: **155 passed**. Production and test TypeScript, changed-file ESLint and Cloud architecture check passed.
- OpenSpec strict validation passed. Migration lint passed; policy is rerun on the committed complete release diff.
- Real Fly HTTP acceptance: new owned test project, local PostgreSQL/PostgREST/S3, real Machine and Volume, production HTTP authorization and separate lifecycle/API file pools. **Passed**, including creation, repeated root access, stream reconnect and three actual stop/start cycles. Owned resources were removed; no existing user workspace was stopped.
- Current configured Supabase + existing real Fly workspace: read-only service status/root samples; no machine start/stop and no project file changes.

## Measurements and limits

See `measurements.json` for every sample, environment and scope.

| Scope | Samples | p50 | Sample p95 |
| --- | ---: | ---: | ---: |
| Configured test Supabase + real Fly, warm status/root | 10 | 2.388 s | 3.927 s |
| Configured test Supabase + real Fly, file connection recreated on retained SSH | 5 | 5.804 s | 8.733 s |
| Owned local PG/S3 + real Fly HTTP, actual stopped Machine resume | 3 | 13.033 s | 17.541 s |

The first resume run was 19.29–23.19 s. Timing identified unnecessary private-proxy teardown/start plus duplicate allocation preflight. Preserving the bound private route and authenticating existing resources through actual inspect/resume removed those waits. Final observed resume samples: 13.033, 12.510, 17.541 s.

These are sample percentiles, **not a production SLO acceptance**. Resume uses local PG/S3; remote-database samples cover warm/reconnect only. Measurements exclude Desktop project-context resolution and rendering; first clone is separate (recorded in JSON). Billing was disabled in provider acceptance. API process restart still loses its SSH cache; a fully cold connection remains slower. No multi-instance unique-connection ownership is claimed.

## Database merge admission

Read-only inspection of Qubits found 54 applied versions, ending at `20261023010000`. The complete missing set is the preceding committed `20261023020000` plus this change's `20261023030000`; there are no unexplained older gaps. Both are additive, independent SQL with no new data-migration prerequisite. Entire pending chain and populated upgrade were tested locally. Hosted SQL must run through Qubits CI/CD before activating the new local backend. No remote application deployment is requested.

## Integration and activation

- Backend implementation `accefcf6` merged through [PR 1408](https://github.com/puppyone-ai/puppyone-cloud/pull/1408) into Qubits as `df504964`. Required branch checks passed; the optional Supabase Preview check did not pass and is not used as target deployment evidence.
- [Deploy Database to Qubits, run 39929659325](https://github.com/puppyone-ai/puppyone-cloud/actions/runs/39929659325) succeeded. A subsequent read-only target-ledger inspection confirmed both `20261023020000` and `20261023030000` applied.
- Local canonical backend fast-forwarded to the merged implementation. All 17 pre-existing modified files remained byte-for-byte unchanged. The local supervisor reloaded API and worker on 2026-10-23 at 17:13 Asia/Singapore; the API completed startup and `/health` returned `status: ready`.
- Desktop implementation integrated as `93de9e3a`; 36 integration cases passed in the canonical checkout. The existing Electron/Vite development session uses that checkout.
- After backend integration, 50 focused SSH, preflight, consumer-readiness and security tests passed in the canonical checkout using the running services' Python environment.
- Additional release checks: 262 database release regression tests passed (20 skipped); 17 PostgREST/RPC contract tests passed; 35 focused tests passed in the actual local runtime environment. Correct-source migration lint checked 14 artifacts and policy checked both pending SQL paths.
- Canonical architecture documentation was updated in `puppy-issues/document` with backend and desktop source provenance. Its validator passed across 433 Markdown documents, retaining 25 pre-existing cross-repository link warnings. Those documentation edits remain uncommitted alongside existing user edits.
- Warm timings observed before the 17:13 reload belonged to the earlier runtime's already-warm connection. They must not be attributed to this implementation. The measured results above come from the explicitly described acceptance/probe environments.
