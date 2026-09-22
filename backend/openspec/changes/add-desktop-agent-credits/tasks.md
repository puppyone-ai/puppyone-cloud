## 1. Financial authority
- [x] Add personal wallet, immutable purchase, reservation and ledger migration.
- [x] Implement idempotent checkout, payment/refund validation and credit settlement.
- [x] Recover interrupted provider requests and validate concurrent admission.

## 2. Cloud inference boundary
- [x] Add authenticated personal credit BFF and bounded streaming inference.
- [x] Verify authorization, replay, disconnect and provider failure behavior.

## 3. Desktop and sandbox
- [x] Add revocable managed model connection and email/payment UI.
- [x] Configure Polar sandbox product, signed webhook and test deployments.
- [ ] Complete real sandbox checkout, credit grant and local Agent tool turn.
- [x] Update canonical documentation and record validation evidence.

## Acceptance checkpoint — 2026-09-21

Cloud auth/billing/managed-AI tests: 133 passed. Desktop related tests: 78 passed.
PuppyPay suite: 124 passed, 2 local-PostgreSQL conditional cases skipped;
equivalent financial concurrency, duplicate payment, settlement and taxed-refund
scenarios passed using isolated tables in the actual test PostgreSQL database.
All four sandbox service deployments succeeded. No paid-user acceptance is
claimed: a test email/login is still required before Polar sandbox payment,
credit grant and a locally executed Agent tool turn can be verified together.

Canonical contract and receipt:
`puppy-issues/document/puppyone-desktop/model-connections/managed-inference-and-credits.md`
and `evidence/2026-09-21-managed-agent-credits.md` in that domain.
