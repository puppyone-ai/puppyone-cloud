# Sprites runtime provider

`sprites` implements the existing `RuntimeProvider` boundary. Agent tasks,
shells, live file reads/writes and file events use the same worker and native
workspace directory as the other providers. No published Git/S3 copy substitutes
for live files. The database schema and public file APIs are unchanged.

Sprites is the source and example-environment default for new workspaces.
Hosted acceptance is required before enabling it in a deployment.

## Build and configure

From `backend/`, build the worker and export its Node binary, worker code and
installed dependencies as a digest-pinned bundle:

```sh
docker build --platform linux/amd64 -t puppyone-sprites:local agent-worker
uv run python agent-worker/build_sprites_bundle.py \
  --image puppyone-sprites:local --output /absolute/path/sprites-worker.tar.gz
```

The export prints `bundle` and `sha256`. Install that bundle in the API and sandbox
worker deployments and configure both with:

```dotenv
CLOUD_AGENT_SANDBOX_PROVIDER=sprites
CLOUD_AGENT_EXISTING_SANDBOX_PROVIDERS=fly
CLOUD_AGENT_SPRITES_TOKEN=<Sprites organization token>
CLOUD_AGENT_SPRITES_BUNDLE=/absolute/path/sprites-worker.tar.gz
CLOUD_AGENT_SPRITES_SHA256=<64 hex characters printed by the build>
SANDBOX_REAPER_ENABLED=true
SANDBOX_REAPER_INTERVAL_S=120
```

Credentials stay in the backend. Sprites does not use `CLOUD_AGENT_FLY_*`, Fly
Machines, Fly Volumes, or an E2B API key for this workspace path. The separate
legacy `SANDBOX_TYPE` setting is unchanged. Existing Fly workspace bindings remain
pinned and are served through `CLOUD_AGENT_EXISTING_SANDBOX_PROVIDERS` (default
`fly`). Keep their original `CLOUD_AGENT_FLY_*` credentials and transport available.
API admission, Agent capability checks, pending-open dispatch and maintenance
honor that retained-provider list. New workspaces always use the default; an error
never falls back to Fly. Add `e2b` to the comma-separated retained-provider list
only if existing E2B workspaces must also be served. Remove a retained provider
only after its bindings are retired or migrated. No files are moved automatically.
Local Docker development must explicitly set `CLOUD_AGENT_SANDBOX_PROVIDER=docker`.
For a rolling deployment, update the sandbox and Agent workers with this provider,
bundle and credentials before switching the API's default; older workers cannot
execute Sprites bindings.

Fresh allocation uploads and verifies the bundle before admitting tasks. A root
bootstrap installs missing system tools, then seals task egress with the existing
UID firewall and a Sprites network policy. Runtime code and Node are pinned by the
bundle; the provider base OS and apt system packages are not part of that digest.
The bundle is limited to 256 MiB. The first boot must support root `sudo`, nftables,
bind mounts and the Sprites management socket; failures leave admission closed.
The management directory must be root-owned and not user-writable. The platform
socket is mounted read-only: startup preserves it at a root-only bind alias under
`/run/puppyone-sprites`, then masks the original path with an inaccessible file.
Lifecycle calls use the private alias; ordinary tasks can access neither path.
Startup restores these mounts after cold boot before admitting any task.
Subsequent wakes reuse the installed artifact and files, without package downloads.

## Lifecycle and preservation

- One deterministic Sprite name per workspace generation; labels and the immutable
  Sprite ID fence attachment, deletion and retries after an interrupted allocation.
- `/workspace` and root-owned lifecycle state use durable directories in the
  Sprite filesystem. Cold wakes restart the file service. Files are retained;
  running processes and memory are **not** promised to survive a pause.
- The existing lifecycle owner renews a five-minute Sprites task hold. There is
  no independent heartbeat that keeps an abandoned workspace alive indefinitely.
  The sandbox worker's workspace maintenance runs every 30 seconds; the separate
  `SANDBOX_REAPER_INTERVAL_S` setting controls legacy cleanup, not this hold.
- Pause releases the hold, stops the managed file service, terminates remaining
  exec bridges, and waits up to two minutes for platform status `warm` or `cold`.
  Real metadata reads can briefly return an older cold/warm observation. Active
  attachment retries metadata without waking compute; pause always performs the
  release/stop protocol and rejects a stale creation snapshot as its idle ACK.
  An error or timeout does not report a successful pause. Resume explicitly wakes
  the Sprite and verifies the file service before admitting requests.
- Existing idle policy is unchanged. Saving a file does not immediately suspend
  the workspace. An open file watcher may keep an exec connection active until
  lifecycle pause closes it. This adapter does not establish a pricing or scale
  guarantee; account quotas and billing must be verified with Sprites.
- Legacy Agent snapshot recovery is unsupported, as with the Fly volume provider.

## Verification

Local contract tests use an HTTP peer and a real localhost WebSocket server:

```sh
uv run pytest -q tests/agent/runtime/test_sprites_provider.py \
  tests/agent/runtime/test_provider_extension.py \
  tests/agent/runtime/test_runtime_providers.py \
  tests/agent/runtime/test_workspace_provider_cutover.py tests/core/test_config_agent_provider.py
```

The built bundle also has a network-isolated Linux-container check. It exercises
the actual installer, native file service, UID permissions, hold protocol and file
preservation after service restart/remount. It emulates the management socket;
it does not substitute for hosted warm/cold or HTTP API acceptance. Its receipt is
`tests/agent/workbench_acceptance/sprites-local-receipt.json`.

```sh
docker run --rm --platform linux/amd64 --network none \
  --cap-add SYS_ADMIN --cap-add NET_ADMIN --security-opt apparmor=unconfined \
  --mount type=bind,source=/absolute/path/sprites-worker.tar.gz,target=/test/worker.tar.gz,readonly \
  --mount type=bind,source="$PWD/tests/agent/workbench_acceptance/sprites_local.py",target=/test/accept.py,readonly \
  --mount type=bind,source="$PWD/src/platform/sandbox/providers/sprites/bootstrap.py",target=/test/bootstrap.py,readonly \
  --entrypoint python3 puppyone-sprites:local \
  /test/accept.py /test/worker.tar.gz /test/bootstrap.py
```

Hosted acceptance is explicit and **creates billable Sprites**, then deletes only
the test Project's owned resources. With the configuration above and the existing
local Docker/Postgres acceptance prerequisites:

```sh
CLOUD_AGENT_TEST_SPRITES=1 \
CLOUD_AGENT_TEST_SPRITES_COLD=restore \
CLOUD_AGENT_SPRITES_TEST_RECEIPT=/tmp/sprites-acceptance.json \
uv run pytest -q -s tests/agent/workbench_acceptance/accept_sprites.py
```

This reuses the Fly/E2B product scenario: authenticated Agent execution, concurrent
editor read/write, file watch, task egress isolation, Git publication, workspace
reuse, pause, and reopen with the same files. Model responses are scripted;
provider, worker, file APIs and persistence are real. It is not billing or load
acceptance. Initial hosted checks exposed read-only socket and stale metadata
compatibility issues, both covered by the implementation and regression checks.
The final hosted receipts are required for activation. `restore` runs two owned
fixtures: ordinary warm pause/reopen, and an official checkpoint restore of the
already-paused fixture before product reopen. The latter verifies that an
unrelated process was lost and the saved live files remain usable after runtime
reconstruction. This is a deterministic recovery test, not evidence of the
platform's natural cold-eviction timing; checkpoints are not used by the runtime
provider's normal pause/resume path. Receipts get `-warm` and `-restore` suffixes.
`CLOUD_AGENT_TEST_SPRITES_COLD=1` separately opts into waiting up to 30 minutes for
natural cold eviction and checking a changed kernel boot ID. The platform does
not promise a bounded warm-to-cold interval. Omitting the setting exercises
ordinary pause/reopen only.

API references: [WebSocket exec](https://docs.fly.io/sprites/api/websockets/execute-command),
[task holds](https://docs.fly.io/sprites/keeping-sprites-running),
[services](https://docs.fly.io/sprites/api/services/create-service).
