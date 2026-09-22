# Payment, Billing, Entitlements, and Usage Architecture

> Scope update — 2026-09-21: this is the organization/Runtime billing contract. The current Desktop Agent sandbox uses [personal credits and a model inference gateway](../../backend/src/platform/managed_ai/README.md); it does not enable hosted Agent execution, RU billing or the project-upload conversion loop.

> Status: active architecture contract for the PuppyOne product plane
> Implementation baseline: `puppyone@cf6dc22e+dirty`, `PuppyPay@b73a722`,
> `puppyone-desktop@7e0a9e8+dirty`
> Last reviewed: 2026-07-15

This document defines how payment, subscription billing, entitlements, seats,
runtime usage, and storage quota fit into PuppyOne. It is the canonical
integration contract in the PuppyOne repository. It does not duplicate the
executable price catalog: commercial amounts and plan formulas remain owned by
PuppyPay.

Related sources:

- `backend/openspec/changes/add-unified-billing-control-plane/` defines the
  approved change requirements and rollout tasks.
- PuppyPay's `docs/architecture/billing-entitlements-and-runtime-metering.md`
  defines the commercial control plane and provider internals.
- PuppyPay's `docs/product/pricing-and-billing-rules.md` explains the current
  product policy. It is descriptive; the versioned PuppyPay catalog is the
  executable source.
- `ISSUE-033` in `puppyone-ai/puppy-issues` tracks production rollout evidence.
- `ISSUE-038` records the completed Desktop control-plane state hardening
  merged in `puppyone-desktop@7e0a9e8`.

## 1. Terms that must remain separate

| Term | Meaning | Authority |
| --- | --- | --- |
| Payment | Collection, tax, invoice, refund, and provider lifecycle | Polar, normalized and verified by PuppyPay |
| Billing | Catalog, quote, subscription, purchased seats, and monetary state | PuppyPay |
| Entitlement | Versioned commercial result: features, limits, allowed values, seats, and effective time | PuppyPay publishes; PuppyOne validates and projects |
| Authorization | Whether this principal may act on this organization, Project, or resource | PuppyOne |
| Metering | Durable record of runtime and logical storage actually consumed | PuppyOne records product facts; PuppyPay owns runtime credit accounting |
| Presentation | Display catalog, quote, usage, pending state, and provider URLs | Desktop/Web; never authoritative |

Payment success is not authorization. A plan name is not authorization. An
entitlement can only narrow a request that already passed PuppyOne identity and
authorization checks; it cannot grant Organization membership, Project access,
or a runtime principal new authority.

## 2. System boundary

```text
PuppyOne Desktop
  | user session; no provider secret or executable price rule
  v
PuppyOne public API
  |-- human authorization and Organization owner check
  |-- Billing BFF and response validation
  |-- product facts and server-side entitlement enforcement
  |-- local entitlement projection, operations, runtime runs, storage counters
  |
  | service credential + verified actor context
  v
PuppyPay Billing Control Plane
  |-- versioned catalog, quotes, subscriptions, purchased seats
  |-- verified provider lifecycle and entitlement outbox
  |-- runtime credit reservations and settlement ledger
  |
  v
Polar
  | checkout, payment method, tax, invoice, subscription, refund, meter

PuppyPay entitlement outbox
  --> PuppyOne internal publication API
  --> monotonic organization_entitlements projection

PuppyOne hosted runtime
  --> reserve --> heartbeat --> settle/cancel --> PuppyPay runtime ledger
```

There are two databases and no distributed transaction or cross-database
foreign key:

- PuppyPay PostgreSQL owns commercial and financial state.
- PuppyOne Supabase owns organizations, members, product execution facts,
  billing operations, runtime run records, logical storage counters, and the
  low-latency entitlement projection.

Convergence uses outboxes, stable idempotency keys, monotonic revisions,
payload hashes, leases/fencing, and reconciliation.

## 3. Source-of-truth matrix

| Fact | Single source | Projections and consumers |
| --- | --- | --- |
| Prices, plan boundaries, included storage/RU, provider mapping | PuppyPay versioned catalog and private deployment config | PuppyOne sanitizes/forwards; Desktop renders |
| Provider customer, order, invoice, refund, subscription event | Polar event after PuppyPay verification | PuppyPay normalized commercial state |
| Current plan, purchased seats, quote, pending commercial change | PuppyPay | PuppyOne BFF reads it; entitlement snapshot carries the enforceable result |
| Organization, member, role, actual billable capability | PuppyOne | PuppyPay receives idempotent seat proposals and reconciliation facts |
| Human and machine authorization | PuppyOne authorization services | Entitlement checks may only restrict the authorized action |
| Effective feature/limit/allow values | Accepted PuppyOne entitlement projection | Product entry points enforce locally |
| Hosted runtime run identity and actual lifecycle | PuppyOne | PuppyPay reserves and settles credit against the stable run identity |
| Runtime credit balance and reservation ledger | PuppyPay | PuppyOne receives authorization/settlement results |
| Logical active storage bytes | PuppyOne | PuppyOne enforces and exposes non-financial reconciliation facts |
| UI cache, selected plan, open quote | No authority | Disposable Desktop state scoped to one account and organization |

Neither Desktop nor PuppyOne may recreate the Free/Plus/Business price and
benefit matrix as executable policy. PuppyOne may understand generic contract
keys such as `storage.max_bytes` or `runtime.included_units`, but PuppyPay alone
decides their values for a commercial plan.

## 4. Non-negotiable invariants

1. PuppyOne authenticates and authorizes first. A `RuntimeGrant`, access key, or
   other machine credential cannot enter Billing, Team, member, or credential
   management endpoints.
2. The Desktop bearer token terminates at PuppyOne. PuppyPay receives an
   independent service credential plus a verified actor ID/email; PuppyOne does
   not forward the user token as a service credential.
3. Every billing mutation rechecks the current Organization owner role on the
   server. The current implementation recognizes `owner`; any future billing
   manager role must be a server capability, not a client-side role string.
4. A checkout return URL only means that a browser returned. Access changes
   only after a verified provider lifecycle event changes PuppyPay state and a
   newer entitlement revision is accepted by PuppyOne.
5. All cross-service mutations use a stable idempotency key. A retry must return
   the same semantic result or an explicit conflict, never create a second
   charge, quote application, reservation, or settlement.
6. An entitlement publication is monotonic. Same revision and same payload hash
   is an idempotent replay; an older revision or same revision with different
   content is rejected and reconciled.
7. Valid, unexpired published entitlements keep ordinary product reads working
   during a temporary PuppyPay outage. New financial mutations and unreserved
   variable-cost work fail closed.
8. Pending seat purchases are not Organization members and grant no product
   access. Product membership is written only after purchased capacity is
   confirmed and rechecked atomically.
9. Every hosted variable-cost execution surface is either fully metered or
   explicitly blocked in `required` mode. “Run now and account later” is not a
   supported state.
10. Over-quota users retain usage-reducing operations, deletion, and export.
    Quota enforcement blocks growth, not recovery.
11. Community `disabled`/`local` deployments boot without PuppyPay or Polar.
    Official commercial policy is not a self-hosted license mechanism.
12. Client state from one account, API origin, or organization must never be
    rendered or submitted in another context, including late async responses.

## 5. Public and internal contracts

### 5.1 Desktop-facing PuppyOne BFF

The public base path is `/api/v1/billing`. The implemented surface is:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/catalog` | Sanitized public catalog |
| `GET` | `/organizations/{org_id}/summary` | Commercial summary and pending state |
| `GET` | `/organizations/{org_id}/usage` | PuppyPay runtime usage plus PuppyOne logical storage |
| `GET` | `/organizations/{org_id}/operations` | Product-side operation/saga state |
| `POST` | `/organizations/{org_id}/plan/quote` | Immutable plan quote |
| `POST` | `/organizations/{org_id}/seats/quote` | Immutable seat quote, optionally linked to a product operation |
| `POST` | `/organizations/{org_id}/checkout` | Provider checkout from a quote |
| `POST` | `/organizations/{org_id}/plan/change` | Apply a confirmed plan quote |
| `POST` | `/organizations/{org_id}/seats/change` | Apply a confirmed seat quote |
| `POST` | `/organizations/{org_id}/subscription/cancel` | Request cancellation lifecycle |
| `POST` | `/organizations/{org_id}/portal` | Create a short-lived customer portal URL |
| `POST` | `/organizations/{org_id}/runtime/top-up` | Optional runtime top-up checkout |
| `PUT` | `/organizations/{org_id}/runtime/overage` | Optional overage policy |

The BFF enforces its own path allowlist, URL-encodes organization IDs, requires
`Idempotency-Key` on mutations, uses bounded timeouts, and only forwards a
small allowlist of safe upstream error details. Checkout and portal URLs are
HTTPS-only except loopback HTTP in development and are revalidated before the
Desktop opens the system browser.

### 5.2 Service-to-service contracts

PuppyOne calls PuppyPay `/internal/v1/billing` for organization provisioning,
seat proposals, runtime reserve/heartbeat/settle/cancel, and reconciliation.
PuppyPay calls PuppyOne internal endpoints to:

- publish an entitlement snapshot;
- verify that an actor still manages the target organization;
- read non-financial organization facts for reconciliation.

These calls use a dedicated internal secret that must differ from PuppyOne's
other internal API credential. Hosted environments require HTTPS and reject a
missing, short, or contradictory configuration at startup.

Contract compatibility rules:

- additive optional fields are backward compatible;
- field deletion, unit changes, or semantic changes require a major version;
- unknown features are closed;
- unknown mandatory limits or an unsupported major version reject the snapshot;
- the public catalog never includes provider product/price IDs.

## 6. Core flows

### 6.1 Organization provisioning

1. PuppyOne creates the Organization and a durable
   `entitlement_provision` operation with a stable idempotency key.
2. A provisioner calls PuppyPay as a service and includes the initial billing
   manager identity.
3. PuppyPay creates or finds the billing account, creates the initial commercial
   state, and emits its first entitlement publication.
4. PuppyOne validates and atomically stores the snapshot.
5. A leased worker retries incomplete work. A late initialization response can
   never replace a newer paid revision.

### 6.2 Catalog, quote, checkout, and plan change

1. Desktop resolves an explicit Organization context.
2. PuppyOne validates the user session and current owner role.
3. Desktop requests a server quote; it never computes the payable amount.
4. Quote confirmation either applies a server-side subscription change or
   creates a provider checkout URL.
5. Desktop opens the URL and enters a pending state.
6. Polar sends a signed webhook to PuppyPay. The event is persisted before
   side effects and processed idempotently.
7. PuppyPay updates commercial state and emits a higher entitlement revision.
8. PuppyOne accepts the projection; Desktop polling observes authoritative
   summary/operation state. The browser return alone changes nothing.

### 6.3 Seat change saga

PuppyOne membership is the product fact; purchased quantity is a commercial
fact. A seat increase therefore cannot be one cross-database transaction.

1. Under product-side locking, PuppyOne calculates active billable humans and
   short-lived admission reservations.
2. If capacity exists, an operation leases the capacity while membership is
   written, then completes.
3. If capacity is insufficient, PuppyOne creates an
   `awaiting_confirmation` operation and does not create membership.
4. A background proposal worker may obtain a quote using a lease and fencing
   token, but it cannot accept the quote or grant access.
5. An owner confirms the quote through the BFF.
6. Verified provider lifecycle processing publishes enough purchased seats.
7. The original member action is retried; PuppyOne atomically rechecks current
   entitlement and only then writes membership.
8. Removal revokes product access immediately and records an idempotent
   commercial decrease for reconciliation.

Billable seats are capability-derived active humans. Machine principals and
pending or read-only/no-runtime humans are not billable. Role names are an
implementation input, not the commercial authority.

### 6.4 Entitlement publication and enforcement

An accepted snapshot includes at least schema/catalog versions, organization,
plan/status, seat quantity, source revision, effective interval, entitlement
payload, source event, and SHA-256 payload hash.

PuppyOne's publication RPC locks the organization projection, rejects
regression/conflict, writes the new snapshot, and appends an audit event in the
same database transaction. Normal product requests read this local projection;
Polar and PuppyPay are not on the synchronous read path.

General product enforcement follows `disabled -> shadow -> required`:

- `disabled`: do not evaluate commercial limits;
- `shadow`: evaluate and record would-deny/unavailable outcomes, but preserve
  pre-cutover behavior;
- `required`: enforce feature, capacity, and allow-list decisions server-side.

### 6.5 Hosted runtime

1. A product surface creates a stable run ID and immutable billing identity.
2. PuppyOne persists a local runtime run and reserves an upper-bound number of
   units from PuppyPay before starting work.
3. Long work heartbeats the lease.
4. Success, failure, cancellation, and timeout all settle or cancel through the
   same durable run identity.
5. Recovery claims abandoned local records with fencing and replays the frozen
   settlement payload idempotently.
6. PuppyPay releases unused reservation, records consumed units, and may
   asynchronously report provider meter usage.

Automation, Hosted Agent, and Scope Sandbox have integrated lifecycle paths.
Remote Workspace creation and OCR/Smart Parse remain explicitly unsupported in
`required` mode until their complete lifecycle has a stable metering identity.
Desktop-local runtime is never hosted RU.

### 6.6 Logical storage

Logical active content, not provider object bytes, is the quota unit. Version
Engine publish computes the tree delta once at the shared mutation boundary.
The canonical Project-root CAS, entitlement revision check, quota decision,
version publication, and organization counter update occur in one database
transaction. This avoids per-client enforcement and check-then-write races.

Incremental counters keep the hot path cheap. A leased full-tree reconciler
repairs drift and records threshold changes. Rename/reuse semantics are based on
logical content, and the system continues to permit deletion/export when over
quota.

## 7. Durable PuppyOne state

The additive migration
`supabase/migrations/20260714010000_unified_billing_control_plane.sql` owns the
current product-plane state:

| State | Purpose |
| --- | --- |
| `organization_entitlements` | Current low-latency commercial projection with revision/hash/effective metadata |
| `organization_entitlement_events` | Append-only publication audit history |
| `organization_billing_operations` | Provisioning, seat saga, retry, quote linkage, and product-side idempotency |
| `runtime_billing_runs` | Immutable run identity, reservation, heartbeat, frozen settlement, recovery status |
| Organization usage counters/events | Logical storage value, version, threshold, idempotency, and reconciliation lease |

Row-level security keeps operational mutation service-only. Member-visible
projection reads do not make members billing managers; every billing mutation
still rechecks owner authority.

## 8. Consistency and recovery

| Boundary | Mechanism | Conflict behavior |
| --- | --- | --- |
| Client -> PuppyOne -> PuppyPay mutation | Stable `Idempotency-Key` plus request payload binding | Same intent replays; changed payload conflicts |
| PuppyPay -> PuppyOne entitlement | Increasing `source_revision` plus `payload_hash` | Old revision rejected; same revision/different hash rejected |
| Seat/provision workers | Durable operation, lease, `updated_at` fencing | Stale worker cannot overwrite newer owner action |
| Runtime settlement | Durable local run, frozen payload, reservation/run identity, fencing | Retry cannot recompute or double settle |
| Storage publish | Database transaction and entitlement revision match | Stale precheck or quota breach rolls back publication and counter |
| Provider webhook | Signature verification, durable inbox, delivery ID, per-org processing | Duplicate delivery acknowledges without duplicate effects |
| Cross-system drift | Scheduled reconciliation and auditable controlled repair | No silent rewrite of financial history |

There is no “last response wins” exception for UI state. Desktop must apply the
same consistency discipline at its boundary: a response may update state only
if its account/organization context and request epoch are still current.

## 9. Failure policy

| Failure | Required behavior |
| --- | --- |
| Catalog unavailable or invalid | Show unavailable/retry; never render stale executable prices as current |
| PuppyPay unavailable during quote/change | Fail the new mutation with a stable retryable error; do not infer success |
| PuppyPay unavailable during ordinary read | Continue from a valid unexpired local projection where the endpoint permits it |
| Missing/invalid/expired projection in required mode | Fail closed for new access, growth, and variable-cost work |
| Runtime reservation unavailable/insufficient | Do not start hosted work |
| Storage would exceed quota | Roll back the growth mutation; preserve read/delete/export |
| Duplicate/out-of-order webhook/publication | Idempotently acknowledge exact replay; reject regression/conflict |
| Checkout browser returns before webhook | Remain pending and poll authoritative summary/operations |
| Organization/member support read partially fails | Show an indeterminate error; never reinterpret missing members as “not owner” |
| Account/API-origin/organization changes during request | Reset context-owned state and ignore/abort late responses |
| Poll tick overlaps an earlier load | Single-flight or serialize; older completion cannot overwrite newer state |

## 10. Deployment and rollout modes

| Setting | Values | Responsibility |
| --- | --- | --- |
| `ENTITLEMENTS_MODE` | `disabled`, `local`, `db` | Projection source |
| `BILLING_ENFORCEMENT` | `disabled`, `shadow`, `required` | General feature/limit enforcement |
| `BILLING_UI_ENABLED` | boolean | Expose BFF reads/UI |
| `BILLING_WRITES_ENABLED` | boolean | Permit financial mutations; requires UI enabled |
| `SEAT_BILLING_MODE` | `disabled`, `shadow`, `required` | Seat admission and proposal behavior |
| `RUNTIME_METERING_MODE` | `disabled`, `shadow`, `required` | Hosted runtime reservation and settlement |
| `STORAGE_ENFORCEMENT_MODE` | `disabled`, `shadow`, `required` | Logical storage quota |

Official hosted production targets `db` plus `required`, but each subsystem is
promoted independently after migration, parity, shadow, reconciliation, and
canary evidence. Community defaults remain `disabled`; `local` is an explicit
self-hosted/test quota configuration and not an official commercial plan.

Startup rejects contradictory hosted configuration, missing PuppyPay URL or
service secret, insecure hosted HTTP, reuse of an unrelated internal secret,
and write-without-UI combinations. Turning on a UI flag is not sufficient to
turn on enforcement.

## 11. Desktop control-plane state contract

The Billing page is global to an Organization, not to the currently open local
workspace. Its minimum context key is:

```text
user_id + session_generation + api_origin + organization_id
```

For every context change the client must:

1. abort or epoch-fence catalog/summary/usage/operation requests;
2. clear billing data, selected quote, requested plan, linked operation,
   action errors, polling deadline, and idempotency-key map;
3. positively resolve the intended organization rather than choosing
   `organizations[0]` when multiple organizations are possible;
4. represent member loading, member error, non-owner, and owner as distinct
   states; UI gating is advisory and the BFF remains the authority;
5. avoid passing workspace-scoped Project lists into global Billing. An
   organization project count must come from an organization-scoped API or be
   omitted;
6. run refresh/polling single-flight and stop on authoritative terminal state
   or timeout;
7. validate response organization IDs before committing state;
8. keep mutation idempotency keys stable across a retry of the same intent, but
   never across account/organization context changes.

As of `puppyone-desktop@7e0a9e8`, the page implements this contract through a
context-keyed reducer/controller, separate load and action epochs, single-flight
refresh with one trailing request, terminal/timeout polling, context-owned
idempotency intents, explicit multi-organization selection, distinct member
failure/non-owner states, and removal of workspace Project data from Billing.
Controller, Organization hook, and rendered-page regressions cover the critical
account/Host/Organization and asynchronous failure paths. `ISSUE-038` is closed.

## 12. Security boundary

- Human Billing endpoints use the normal authenticated user dependency and a
  fresh Organization owner lookup. Product/runtime credentials cannot be
  adapted into that dependency.
- PuppyOne-to-PuppyPay and PuppyPay-to-PuppyOne use dedicated service identity;
  actor context is data to revalidate, not a substitute for service auth.
- Gateway paths are allowlisted to prevent arbitrary proxying/SSRF.
- Provider secrets, internal product IDs, raw upstream error payloads, user
  bearer tokens, and entitlement internals are not exposed to Desktop.
- Webhooks are verified against the unmodified raw body before durable inbox
  processing.
- Client-side feature flags, owner checks, disabled buttons, and plan labels are
  UX only. Direct API calls and modified clients still meet server enforcement.
- Logs use organization/run/operation correlation IDs and redacted error codes;
  they must not contain credentials, checkout secrets, provider signatures, or
  full financial payloads.

## 13. Observability and reconciliation

The hosted rollout must observe at least:

- entitlement outbox age, accepted revision, payload-hash conflict, and
  PuppyPay-to-PuppyOne revision lag;
- billing operation age/status/retry count and seat proposal fencing conflicts;
- actual billable members versus purchased seat quantity;
- runtime reserve latency/failure, active lease, orphan, recovery, settlement,
  and PuppyPay-versus-provider meter drift;
- logical storage counter/version/threshold and full-reconciliation drift;
- webhook signature failures, inbox backlog, duplicate rate, processing retry,
  and dead-letter state;
- BFF latency/error class without leaking upstream sensitive details;
- Desktop account-context discard, polling overlap prevention, and terminal
  operation time.

Reconciliation may repair recoverable projections or retry idempotent work. It
must never delete financial history, silently manufacture provider success, or
rewrite an accepted entitlement to an older revision.

## 14. Verification and release gates

Minimum automated coverage:

- catalog privacy and provider-map startup guards;
- BFF authentication, owner recheck, path allowlist, idempotency, timeout, and
  stable error translation;
- entitlement schema/hash/revision/effective-time concurrency;
- seat admission, pending-member isolation, 14-to-15 boundary, lease/fencing,
  removal, and recovery;
- runtime reserve/heartbeat/settle/cancel for success, failure, timeout, crash,
  replay, and unsupported required-mode surfaces;
- storage CAS/quota/counter transaction, zero/unlimited limit, deletion, and
  reconciliation;
- Desktop account/org switch, partial Organization reads, stale response,
  quote/apply retry, external URL safety, polling order, and route flag;
- community disabled/local boot and hosted startup rejection;
- contract drift and database upgrade/rollback/old-application compatibility.

Production payment remains gated by the evidence in `ISSUE-033`: real provider
configuration, Polar sandbox lifecycle, staging migration/restore, security
deployment evidence, shadow reconciliation, canary, and cross-service E2E.
Unit tests and checked OpenSpec tasks do not replace those external gates.

## 15. Implementation map

| Concern | PuppyOne source |
| --- | --- |
| Public Billing BFF | `backend/src/platform/billing/router.py` |
| PuppyPay HTTP boundary and path allowlist | `backend/src/platform/billing/gateway.py` |
| Entitlement validation/enforcement | `backend/src/platform/entitlements/` |
| Organization provisioning | `backend/src/platform/billing/provisioning.py` |
| Seat policy and admission | `backend/src/platform/billing/seats.py` |
| Seat proposal worker | `backend/src/platform/billing/seat_proposals.py` |
| Durable billing operations | `backend/src/platform/billing/operations.py` |
| Runtime metering/recovery | `backend/src/platform/billing/runtime.py` |
| Logical storage quota/reconciliation | `backend/src/platform/billing/storage.py` |
| Reconciliation facts/internal publication | `backend/src/platform/billing/facts.py`, `backend/src/internal/router.py` |
| Runtime/background worker startup | `backend/src/main.py` |
| Additive product-plane schema | `supabase/migrations/20260714010000_unified_billing_control_plane.sql` |
| Desktop Billing page | `puppyone desktop/src/features/cloud/components/CloudBillingPage.tsx` |
| Desktop Billing controller/state | `puppyone desktop/src/features/cloud/billing/` |
| Desktop typed BFF client | `puppyone desktop/src/lib/cloudApi.ts` |

Any change to authority, service trust, entitlement semantics, financial
mutation, seat admission, runtime accounting, or storage enforcement must first
update the relevant OpenSpec change/spec and this document. Pricing changes
belong in the PuppyPay catalog and must not be implemented by editing this
document or PuppyOne client/server constants.
