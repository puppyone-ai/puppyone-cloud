## Context

The commercial path spans PuppyOne Desktop, the open-source PuppyOne server, the private PuppyPay
control plane, Polar, and two PostgreSQL databases. The system must remain usable for community
self-hosting while the hosted service enforces paid seats, storage, and variable-cost runtime.

## Goals / Non-Goals

- Goals:
  - Keep PuppyPay as the only executable pricing and financial authority.
  - Keep PuppyOne as the only organization/member/product-usage and enforcement authority.
  - Make every cross-service mutation idempotent and recoverable without distributed transactions.
  - Keep Desktop presentational; all hard limits remain server-side.
  - Roll out schema and enforcement without an incompatible deployment window.
- Non-goals:
  - Annual discounts, coupons, custom enterprise CPQ, or an application tax engine.
  - A second automation-count billing ledger.
  - Commercial license enforcement for community self-hosted servers.
  - A full redesign of the legacy Web billing UI.

## Decisions

### Decision: HTTP contracts, never a shared runtime package

PuppyOne consumes a pinned PuppyPay OpenAPI/JSON Schema contract through a small Gateway. It does
not import PuppyPay as a Python package. This keeps the open-source server deployable without the
commercial service and prevents in-process pricing forks.

### Decision: two databases, explicit authorities

PuppyPay PostgreSQL owns accounts, subscriptions, quotes, provider events, entitlement
publications, and runtime credit/usage ledgers. PuppyOne Supabase owns organizations, members,
product runs, logical storage usage, product-side billing operations, and a low-latency entitlement
projection. There are no cross-database foreign keys or transactions.

### Decision: outbox + monotonic revision + reconciliation

Entitlement publications carry `schema_version`, `catalog_version`, `source_revision`,
`payload_hash`, `effective_at`, and `effective_until`. PuppyOne atomically accepts a greater
revision, idempotently acknowledges the same revision/hash, and rejects any regression or
same-revision conflict. Seat and runtime operations use stable idempotency keys and durable local
operation records; reconciliation repairs recoverable drift.

Every commercial mutation exposed to Desktop also has a PuppyOne operation identity. PuppyPay
exposes an owner-authorized read of the authoritative, still-actionable Quote. Before invoking a
provider mutation, PuppyOne reads that Quote and the entitlement baseline and commits one uniquely
Quote-linked operation intent. Client-supplied target values are never the authority, and the
existing apply request remains wire-compatible during rolling deployment. PuppyPay includes the
originating Quote identity in the signed entitlement publication when provider metadata supplies
it. PuppyOne confirms the matching operation in the same database transaction that accepts the
newer entitlement revision. An explicit reconciliation function covers the
webhook-before-HTTP-response race. The public BFF maps storage/worker statuses onto a closed,
typed lifecycle (`pending`, `requires_action`, `processing`, `retryable_failed`, `succeeded`,
`canceled`, `failed`) with explicit `terminal` and `retryable` properties; clients never infer
terminality from raw database strings.

The database enforces one operation per organization/Quote. When a membership seat proposal on a
Free organization resolves to checkout rather than an in-place seat change, checkout advances the
existing membership operation; it never creates a competing commercial operation for that Quote.

### Decision: capability-derived billable seats

An active human member is billable when their resolved organization/product capabilities permit
Cloud writes or hosted runtime. Machine and read-only/no-runtime principals do not count. A seat
increase in progress exists only as a billing operation/invitation and is never inserted into
`org_members`; therefore it grants no product access until the subscription entitlement confirms
sufficient purchased seats. The fifteenth seat requires explicit Business quote acceptance.
Seat proposal operations are claimed through a database lease and sent with an operation-stable
idempotency key. The worker may create a Quote but cannot apply it; an organization owner confirms
the commercial change, and a fenced write prevents the worker from overwriting that newer action.

### Decision: runtime reservation before execution

Every hosted compute entry point creates a stable run id, reserves an upper-bound number of runtime
units, heartbeats long leases, and settles actual usage on success, error, cancellation, or timeout.
Workers recover orphaned reservations using the same idempotency identity.

The launch inventory treats Automation, Hosted Agent, and Scope Sandbox as integrated paths.
Remote Workspace creation and OCR/Smart Parse are explicitly unsupported in required mode and fail
closed before provider creation or worker enqueue until their complete lifecycle is metered. Shadow
mode observes those gaps without blocking. Required mode rejects a missing stable run identifier
instead of synthesizing a time-based billing identity.

### Decision: logical storage quota at the product mutation boundary

Storage is measured as logical active content rather than provider object bytes. All mutations
that grow content call one quota service; deletions and exports remain available when over quota.
Incremental counters are reconciled by periodic full scans.

### Decision: progressive rollout

Billing UI and writes are independently gated. Seat, runtime, and storage use
`disabled|shadow|required`. Hosted startup rejects contradictory required-mode configuration;
self-hosted `disabled/local` never contacts PuppyPay.

### Decision: resumable Desktop coordination

Desktop watches a concrete PuppyOne operation with bounded exponential polling and an immediate
refresh when its window regains focus. The watch has no short financial timeout: it ends only when
the server reports a terminal lifecycle or the Billing surface unmounts. A reload discovers the
same non-terminal operation from the organization operation feed and resumes watching it. Quote
expiry and all returned organization/quote/target invariants are validated before an external URL
is opened, while PuppyOne and PuppyPay remain the authoritative validators.

Organization selection is persisted by Cloud origin and user identity, not by an ephemeral login
generation. Billing reads a narrow current-user organization access resource instead of fetching
the full member directory or Team-only entitlement/seat data merely to establish Owner authority.

## Risks / Trade-offs

- Eventual consistency can temporarily show a pending checkout. The UI exposes pending state and
  polls the authoritative PuppyOne projection instead of treating the return URL as success.
- Provider events may arrive before PuppyOne stores the provider response. Because the operation
  intent is committed first, Quote correlation plus idempotent reconciliation closes both event
  orderings without an untracked external side effect or distributed transaction.
- Keeping pending activation outside `org_members` adds an explicit finalization step, but avoids
  changing every authorization/RLS query and makes it structurally impossible for an unpaid seat
  to acquire tenant access.
- A generic operation table is less type-specific than one table per saga. The operation payload is
  schema-versioned and constrained by kind, while runtime high-volume records remain separate.
- Storage full reconciliation is more expensive than trusting deltas. Reconciliation runs
  asynchronously and only corrects drift; hot-path checks use the counter.

## Migration Plan

1. Freeze and test the PuppyPay contract, including authoritative Quote inspection, and reject
   placeholder provider mappings in hosted mode.
2. Apply additive PuppyOne columns/tables/RLS/indexes; old code continues to run.
3. Deploy PuppyPay Quote inspection first, then Gateway/BFF and dual-compatible entitlement code
   with all writes disabled.
4. Republish/bootstrap snapshots and verify revision/hash/seat parity.
5. Enable Desktop read-only catalog/summary/usage; remove old constants.
6. Canary checkout/portal for internal organizations.
7. Run seat, runtime, and storage in shadow; reconcile all unexplained drift.
8. Switch each subsystem to required independently, then add stricter constraints.

Rollback disables new writes/enforcement first. Additive columns and financial/audit events are
retained; no rollback deletes provider or ledger facts.

## Open Questions

- Production Polar product, price, and meter identifiers are deployment secrets/configuration and
  must be supplied outside source control before hosted writes can start.
- Runtime top-up and postpaid overage stay disabled unless their production prices and cost margins
  are explicitly frozen.
