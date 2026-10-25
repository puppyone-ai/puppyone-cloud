# Database Release Governance

Puppyone releases an environment by merging one tested source revision to its
protected branch. `qubits` selects staging; `main` selects production. The
shared workflow automatically waits for candidate CI, then migrates and verifies
Supabase on a GitHub-hosted runner. Compatible SQL requires only the existing
Supabase environment credentials. Application deployment is a separate stage;
its configuration cannot block an ordinary database migration.

When the environment has `PUPPYONE_RELEASE_CONFIG`, the full coordinator follows
successful schema verification with exact-source application deployment and
acceptance. Without that configuration, the workflow explicitly reports
**database migrated; application deployment not configured**. It never writes
an accepted application deployment receipt for a database-only run. Pending
Contract SQL or SQL with declared data prerequisites always routes to the full coordinator and fails if its
required infrastructure is unavailable; it cannot fall back to uncoordinated SQL.

An environment with the GitHub variable `RELEASE_IMAGES=enabled` builds its
application images once per commit in the `images` job: one backend image for
every backend role and one frontend image per environment (Next.js inlines
`NEXT_PUBLIC_*` at build time). Both are smoke-tested, then published to GHCR
pinned by digest. The coordinator points each Railway service at those digests
and verifies the running digest; Railway never rebuilds them. Environments
without the variable keep exact-commit repository builds.

## Migration merge admission

Before production merge, CI starts the last accepted target version in disposable Docker,
creates representative users and repository data, and runs the candidate
upgrade program against the same volumes. The candidate must preserve login,
file contents and permissions, then pass authenticated reads and writes.
Empty-database installation remains a separate required test.

Production does not need candidate schema or data receipts before merge.
Those receipts are outputs of the release after merge. A normal promotion
from Qubits still requires a successful staging database workflow for the exact
promoted head. This database attestation is distinct from application acceptance.
Production branch protection requires `Release upgrade rehearsal`, database
validation, backend/frontend checks and security checks. Production promotion
also requires `Main Release Gate`. Hosted configuration is verified separately
from repository YAML; editing a workflow does not enable branch protection.

The rehearsal resolves the last accepted `puppyone-release` deployment for the
target environment. A reviewed bootstrap SHA is used only while adopting an
environment with no coordinator receipt. Older accepted deployment statuses
remain valid baseline evidence after GitHub marks them inactive. A failed
deployment is never a successful upgrade baseline.

## Qubits testing and database delivery

Qubits uses a database-focused automatic profile. Pull requests and protected
pushes run immutable migration/history policy, manifest validation, RPC signature
checks, database release regression tests and Gitleaks. Its required branch
checks are `Database validation result` and `Run Gitleaks`. Ordinary SQL migration
waits only for those two workflows at the exact merged SHA. The protected
Supabase target, release lock, migration ledger and post-migration database
verification remain mandatory.

Backend/frontend, Agent, Git/S3, populated installation and historical upgrade
suites run locally before submission. Their GitHub workflows remain available
through manual dispatch and run automatically for `main`. Manual dispatch of
`Validate Database Changes` also selects the full database rehearsal profile.
Qubits automatic checks explicitly omit these suites; their green result is not
proof of application acceptance. Record local commands, revision, results and
any untested coverage in the PR. Production gates and coordinated data/Contract
cutover requirements are unchanged.

Typical local checks from `backend/`:

```sh
uv run pytest -q -m 'not e2e and not integration and not network'
uv run python ../scripts/testing/run_repository_hosting.py --native-pg -q \
  -k 'ref_transaction_service or interrupted_push_retry or native_initialization_abandon or standard_git_pack or direct_object_transport or native_size_limits'
```

Use the relevant workflow's commands for frontend, Docker/S3 or full installation
coverage when those boundaries change. Local acceptance never mutates the hosted
Supabase target; only the post-merge release workflow applies hosted migrations.

## Release execution

```text
merge to qubits / main
  -> qubits: database policy + release regressions + Gitleaks
     main: full candidate CI, including populated Docker rehearsal
  -> protected Supabase environment + database-wide release lock
  -> validate complete schema history and the checksum-bound upgrade plan
  -> ordinary SQL:
       apply every missing migration through the official Supabase CLI
       verify exact history, database contracts, Data API containment and drift
       publish database result independently of application configuration
  -> pending Contract or SQL with declared data prerequisites (no candidate SQL applied by the ordinary lane):
       enter full coordinator; recheck actual prerequisites under the same lock
       stop producers -> drain work -> stop consumers and verify writer exit
       create and restore-test a private backup
       compatible schema -> data run + verify -> dependent Contract -> verify DB
  -> when application release is configured, or a cutover requires it:
       build/deploy the exact source SHA to declared application services
       authenticated Git, live workspace and real Agent acceptance
       durable accepted receipt and GitHub deployment success
```

Historical data artifacts do not become prerequisites for every subsequent
SQL deployment merely because they are listed in the full upgrade plan.
The schema stage classifies pending SQL by the existing Contract naming and
`requires-data-migration` declarations. It verifies an already-current schema
even when old data jobs have no receipt. This never completes, skips or writes
a receipt for those artifacts: the configured application coordinator still
runs its complete ordered plan before accepting an application release.
New SQL that depends on data transformation must declare that dependency;
unready destructive cleanup stays outside active schema history.

The only exception is a reviewed, source-controlled deferral in
`supabase/releases/deferred-data.json`. Staging adopted the native engine through
bounded per-project selections, so it defers the whole-environment
`native-inventory` and destructive `native-recovery` phases. A deferred phase is
never executed, skipped with a receipt or reported complete; the accepted
result lists it under `deferred_data_phases`. Pending SQL that requires a
deferred artifact still fails the release. Production cannot declare deferrals.

Ordinary additive releases keep the application online. Data migrations and
Contract SQL use a maintenance window. Consumers must keep running until their
queues drain; the Synchronize worker is a consumer, while its periodic
producer belongs to the API. A task waiting for user approval is not silently
approved or discarded by release automation. Drain has a bounded timeout and
fails without applying the schema if work cannot finish.

GitHub concurrency queues releases separately for each environment. A
PostgreSQL session advisory lock also serializes the physical database. The
ordinary SQL stage holds it from history inspection through verification; the
full coordinator reacquires the same lock and rechecks state before build,
cutover, deployment and acceptance. The executor checks the owning
connection between operations. Immutable data artifacts retain their own
transaction or advisory locks and restart-safe checkpoints.

Application build/start never performs schema mutation. Railway's Automatic
Deployments setting is disabled for coordinator-owned services. Repository-built
services keep their GitHub connection attached; image-built services have no
repository connection and run only the digest the coordinator sets. The full
coordinator checks the provider's actual autodeploy status and source (repository
or published image) before a coordinated cutover. Independent application
autodeploy must not race a migration; enabling application release requires
coordinator ownership, and its job depends on successful database admission. The
coordinator explicitly deploys its immutable commit, checks every service's
reported commit and health, and never substitutes the latest branch head.
`serviceInstanceDeployV2` returns the deployment ID that subsequent polling verifies.
`Wait for CI` alone cannot own a drain/data/Contract sequence.

## Module and artifact ownership

```text
supabase/
  migrations/                 official Supabase schema history
  data_migrations/<id>/       immutable manifest, execution, verifier, fixtures
  archive/before_b1/          immutable historical migration sources
  baselines/b1/               baseline source inventory and checksums
  releases/
    upgrade-plan.json        ordered schema boundaries and data artifact IDs
    control.sql              private coordinator journal bootstrap
    deployed-bases.json      initial accepted-version adoption pins
backend/src/infra/data_migrations/
  catalog.py, runner.py       existing artifact validation/execution/receipts
  release/
    plan.py                  dependency, retirement and checksum validation
    engine.py                coordinated environment release state machine
    schema_only.py           independent compatible SQL stage and cutover handoff
    target.py, schema.py      shared database operations and Supabase CLI
    journal.py               private release progress and source ordering
    docker.py, hosted.py      actual environment adapters
    railway.py               provider deployment and process-exit boundary
    backup.py, acceptance.py  restore proof and bounded application acceptance
```

Schema SQL uses the official `supabase_migrations.schema_migrations` history.
Data transformations use `public.migration_log` receipts bound to immutable
artifact checksums. `puppyone_release.runs` records source SHA, plan checksum,
phase and private evidence; it is not exposed by PostgREST or granted to
application roles. It is not a replacement schema-history table.

The B1 baseline and pre-B1 archive remain unchanged. Baseline adoption checks
the covered history and public schema fingerprint before replacing tracking
rows. Unknown or incomplete historical histories are rejected. `--include-all`
only selects files; it does not establish data prerequisites.

Each schema phase supplies the official CLI with the already-applied files and
only that phase's pending files. Later pending Contract files are withheld
until their data dependencies verify. SQL `requires-data-migration` markers
must resolve to preceding artifacts with the declared checksum. Retired
verifiers are skipped only after their declared Contract boundary is applied;
removed historical tables must not be recreated to run an obsolete verifier.

New schema-only changes append a migration. New application/S3 data work adds
one immutable data artifact with its own verifier and representative fixtures,
then places its ID between the appropriate schema boundaries in the plan.
Changing an executed artifact's bytes or fabricating completion receipts is
forbidden. `contract.pending.sql` stays outside active schema history until a
complete ordered upgrade passes rehearsal; the live database need not already
have run it before the source can merge.

## Data scope and recovery

A completed data receipt eliminates execution of that transformation on
subsequent ordinary releases. Release acceptance uses one owned test project;
it does not audit all customer repositories or scan the bucket. A one-time
legacy conversion may legitimately visit every row/object within that
migration's declared scope. That cost belongs to the immutable migration,
never to routine deployment or application startup.

Historical business classifications are explicit reviewed inputs with source
fingerprints. The executor checks them and applies the existing artifact;
it never guesses whether a legacy source is an Import, Synchronize binding or
both. Such input must be prepared for the historical cutover before release.
New releases after that Contract do not require those classifications again.

Only breaking upgrades take a recovery point. Their recovery storage (a separate
private versioned backup bucket and a versioned object bucket) is proven before
any service stops, so an environment without it fails while still online.
Ordinary additive releases neither need nor inspect it. Breaking upgrades stop
writers before taking their recovery point. The backup
includes Cloud public data, Auth identities and migration/release histories;
it is restore-tested in a disposable PostgreSQL container before mutation.
Hosted backups use a separate private, versioned object bucket and are read
back to verify their digest. The application object bucket retains object
versions, including versions deleted by a data transformation. A logical
database restore alone is not a complete object-store rollback.

The Cloud release owns `public`, its migration history and its private release
journal. PuppyPay owns its private financial schema and release history.
Neither Cloud installation nor public CI needs PuppyPay tables or credentials.
Public-schema drift checks must not adopt or delete another service's schema.

## Failure, retry and evidence

Every phase completes before the next begins. Failed data verification blocks
the dependent Contract. Failed schema verification blocks deployment. Failed
application acceptance leaves the release unaccepted. Migration receipts and
release checkpoints survive runner exit, so retry checks actual state and
resumes pending work; it does not rerun a completed data copy.

An accepted source is an idempotent no-op. A source older than the environment's
recorded release, or a divergent sibling, is rejected. A descendant may repair
a failed release, preserving the failed attempt's evidence. Changing the plan
for an existing source SHA is rejected.

Automation never restarts an old binary against a partially upgraded schema.
A breaking release may therefore remain in maintenance after failure. Retry
the same revision or ship a forward fix through the same coordinator. A data
restore is an explicit incident operation using the verified database backup
and corresponding object versions. Read-only diagnosis never writes a success
receipt or bypasses the environment lock.

The accepted receipt means database verification, exact source deployment and
application checks passed. A green build, a migration receipt or a healthy
container alone is insufficient. Logs and backup contents stay private;
public workflow output contains source, phase outcome and aggregate test
results, without customer IDs, object paths, SQL errors or credentials.

## Hosted activation contract

Every protected merge runs CI and the ordinary schema stage automatically.
That stage uses `DATABASE_URL`, `SUPABASE_PROJECT_ID` and
`SUPABASE_ACCESS_TOKEN` from the existing GitHub environment. The official
Supabase CLI and locked Python migration runtime are installed on the runner;
there is no Redis, Railway, S3 or self-hosted-runner requirement for SQL.

Application deployment and data/Contract cutovers additionally need the
following infrastructure contract:

- GitHub-hosted `ubuntu-24.04` runners. The reusable release workflow admits
  only protected `qubits`/`main` push or manual-dispatch events from the
  canonical repository. PR jobs never receive release credentials. No
  self-hosted machine, runner group or organization runner permission is needed.
- GitHub environment branch policies and required checks that enforce the
  source boundary without a routine manual approval between release phases.
- An environment-scoped `PUPPYONE_RELEASE_CONFIG` secret containing the
  existing format-version-1 release configuration: environment identity,
  Railway project token and full API/frontend/Agent/Upload/Import/Synchronize/MCP
  service inventory, Redis queue names, S3 storage, a dedicated acceptance
  account and, only for breaking releases, a private versioned backup bucket.
  Repository-built environments also declare public frontend build arguments;
  image environments instead set `RELEASE_IMAGES=enabled` and the
  `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_APP_URL`, `NEXT_PUBLIC_SUPABASE_URL`
  (plus optional `NEXT_PUBLIC_AUTH_EMAIL_ONLY`, `NEXT_PUBLIC_DEV_MODE`) GitHub
  environment variables and the `NEXT_PUBLIC_SUPABASE_ANON_KEY` environment
  secret (public by design, but kept out of public run logs), and give Railway pull
  access to the GHCR packages (`railway.registry_credentials` with a
  `read:packages` token, or public packages). Image-sourced services run the
  image's start dispatcher, so their health checks, draining time and restart
  policy are service settings rather than `railway.toml`.
  Existing `DATABASE_URL`, `SUPABASE_PROJECT_ID`, `SUPABASE_ACCESS_TOKEN` and
  `SUPABASE_SERVICE_ROLE_KEY` environment secrets bind database authority;
  the JSON document cannot override that binding. After candidate CI passes,
  `supabase/releases/github_actions.py` creates a mode-0600 configuration under
  `$RUNNER_TEMP/puppyone-release-private`, removed at job completion.
- The workflow installs Python 3.12, locked backend dependencies, Node build
  support, PostgreSQL 17 tools and the pinned Supabase CLI, using Docker on the
  GitHub VM. Recovery points remain in the private versioned S3 backup bucket
  and the release journal remains in PostgreSQL. Raw diagnostics stay private
  in the temporary directory and are deleted with it; only the sanitized
  release result is retained as a GitHub artifact.
- For environments with a Fly sandbox: environment variables
  `CLOUD_AGENT_FLY_APP`, `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID` and
  `RAILWAY_SANDBOX_IMAGE_SERVICES`, and secrets `CLOUD_AGENT_FLY_TOKEN` (registry
  push) and `RAILWAY_TOKEN` (a project token scoped to that one Railway
  environment). The `sandbox_image` job publishes and pins the image before any
  application deployment; without `RAILWAY_TOKEN` it publishes and reports that
  nothing was pinned. Environments without `CLOUD_AGENT_FLY_APP` skip it.
- GitHub environment deployment-branch policies limit each environment's secrets
  to its protected branches.
- Disabled independent Railway and Supabase schema deployment bindings. The
  shared workflow is the only normal schema writer; its full coordinator owns
  data cutovers and configured application deployment.

Missing application configuration never blocks compatible SQL. The application
stage is explicitly reported as not configured and does not deploy anything.
When pending SQL declares a data prerequisite or Contract cutover, missing
configuration fails the workflow before candidate SQL; no migration success
is reported. A failed application stage after compatible SQL reports the
database as ready and the application as failed. Repository implementation,
database readiness and actual application activation are separate evidence.

`pull_request_target` admission uses the target branch's trusted workflow, not
the proposed replacement. A gate change must first reach that protected target
through its existing owner-controlled admission procedure before it can govern
subsequent promotions. This one-time gate handover never requires applying the
candidate database migrations before merge.

## Verification contract

Tests cover an empty installation; the deployed old version with real fixture
data; repeated execution; interruption after a committed data phase; Contract
and acceptance failure; stale-source rejection; database-level concurrent
release exclusion; connection loss; private journal permissions; exact
deployment SHA; and consumer drain ordering. Integration fixtures own their
database and object volumes and never accept production credentials.

Docker acceptance checks old file contents and sessions after upgrade, then
performs new authenticated writes and checks Data API containment. The hosted
bounded check first verifies the published file, then follows Desktop's
`GET/POST /projects/{id}/workbench` opening sequence. Initial allocation has a
180-second request budget. The live file list/read/write APIs must preserve
the edit's revision, leave the published Git content unchanged, and let the
real Agent read that unpublished edit from the same workspace. A missing
workbench route, mismatched checkout or stale worker blocks release acceptance;
successful Git hosting alone does not prove Desktop Cloud readiness.
Runtime suites separately cover sandbox reads/writes, publication, concurrency
and database-request budgets. Those performance contracts remain part of the
local/manual Qubits checks and automatic production Agent CI, not a repeated
whole-customer-data release audit.

## Upstream alignment

Schema history follows the [Supabase CLI](https://supabase.com/docs/reference/cli/supabase-db-push).
Release queuing follows [GitHub concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
Private runners follow [GitHub runner security](https://docs.github.com/en/actions/reference/security/secure-use).
Provider deployment uses the [Railway service API](https://docs.railway.com/integrations/api/manage-services).
