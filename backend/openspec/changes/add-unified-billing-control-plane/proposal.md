# Change: Add the unified PuppyPay billing control plane

## Why

PuppyPay already owns the versioned commercial catalog, provider lifecycle, entitlement
publication, and runtime ledger, while PuppyOne still applies an obsolete local plan matrix and
Desktop still renders hard-coded prices. This split can charge one amount, display another, and
grant product access without a durable seat or runtime billing transaction.

## What Changes

- Add a PuppyOne Billing Gateway and authenticated BFF for PuppyPay catalog, summary, usage,
  quotes, checkout, subscription changes, runtime top-ups, and portal operations.
- Extend the PuppyOne entitlement projection with version, seat, effective-time, and payload-hash
  metadata, enforcing monotonic idempotent publication.
- Add durable product-side billing operations for seat sagas, runtime reservations/settlements,
  checkout/subscription mutations, and storage usage counters. Public operation responses expose
  one typed lifecycle instead of leaking worker-specific database statuses.
- Make hosted product enforcement consume only the published snapshot; keep self-hosted
  `disabled` and `local` modes independent from PuppyPay.
- Move Desktop billing presentation to the PuppyOne BFF and remove executable price constants.
- Correlate accepted entitlement revisions back to the quote and durable product operation that
  caused them; Desktop resumes pending operations after reload and never treats a short polling
  timeout or browser return as a financial outcome.
- Read the owner-authorized PuppyPay Quote and commit a unique PuppyOne operation intent before any
  external commercial mutation, closing the provider-success/process-crash gap without trusting
  client targets or breaking the existing apply request contract.
- Introduce disabled/shadow/required rollout modes and reconciliation evidence before production
  billing is enabled.

## Impact

- Affected specs: `billing-control-plane`, `entitlement-enforcement`, `usage-metering`
- Affected code: `backend/src/platform/billing`, `backend/src/platform/entitlements`,
  `backend/src/platform/organization`, hosted runtime/write boundaries, Supabase migrations,
  PuppyPay contracts/configuration, and PuppyOne Desktop Cloud Billing.
- Data migration: additive Supabase schema, active-member backfill, entitlement republish, then
  constraints/cutover. No destructive schema change is permitted in the initial rollout.
