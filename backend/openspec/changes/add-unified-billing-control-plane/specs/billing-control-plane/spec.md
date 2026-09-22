## ADDED Requirements

### Requirement: Single commercial rule authority

The hosted system MUST use the versioned PuppyPay catalog as the only executable source of plan
prices, seat ranges, included storage, and included runtime units. Client and PuppyOne product code
MUST NOT create orders from local price constants.

#### Scenario: Desktop displays a plan

- **WHEN** an authenticated Desktop loads Billing
- **THEN** it receives a sanitized catalog through the PuppyOne BFF
- **AND** the response contains no provider secret or internal product mapping

#### Scenario: Catalog is unavailable

- **WHEN** the catalog cannot be obtained or validated
- **THEN** the client displays an unavailable/retry state
- **AND** it does not fall back to a stale price

### Requirement: Authenticated billing BFF

PuppyOne MUST expose the supported billing read and mutation operations through an authenticated
BFF. Every organization mutation MUST re-evaluate billing-manager authorization and use a stable
idempotency key.

#### Scenario: Non-owner attempts checkout

- **WHEN** an organization member without billing-manager authority requests checkout
- **THEN** PuppyOne denies the request before contacting PuppyPay

#### Scenario: Mutation is retried

- **WHEN** the same mutation and idempotency key are retried
- **THEN** the system returns the original semantic result without creating a second charge or
  subscription mutation

### Requirement: Open-source deployment independence

Community `disabled` and `local` deployments MUST boot and operate without PuppyPay or official
Polar credentials. Hosted required mode MUST fail startup when its billing dependencies are
incomplete or contradictory.

#### Scenario: Community self-host starts

- **WHEN** `ENTITLEMENTS_MODE=disabled` and billing enforcement is disabled
- **THEN** PuppyOne starts without contacting PuppyPay

#### Scenario: Hosted required mode lacks a service credential

- **WHEN** hosted billing is required but the PuppyPay service credential is absent
- **THEN** startup fails with a non-secret configuration error

### Requirement: Provider lifecycle is authoritative

A checkout return URL MUST NOT activate paid access. Only verified provider lifecycle processing,
PuppyPay commercial state, and an accepted entitlement revision may change hosted access.

#### Scenario: User returns before webhook processing

- **WHEN** the browser returns from checkout before the provider event is processed
- **THEN** Desktop displays a pending state
- **AND** paid features remain unchanged until the entitlement revision is accepted

### Requirement: Durable typed billing operation lifecycle

Every checkout and subscription mutation initiated through PuppyOne MUST return a durable
organization-scoped operation. PuppyOne MUST expose a closed public lifecycle with explicit
terminal and retryable semantics, and MUST correlate successful completion to a newer accepted
entitlement revision for the originating Quote. Raw worker/database status strings MUST NOT be the
client contract. A terminal operation MUST NOT regress to a non-terminal state or be rewritten by
a later reconciliation pass. PuppyOne MUST read the authoritative PuppyPay Quote and persist one
uniquely Quote-linked operation intent before invoking the external commercial mutation.

#### Scenario: Process stops after the provider accepts the mutation

- **WHEN** PuppyOne has committed the operation intent and the provider accepts the mutation but
  the BFF process stops before storing the HTTP response
- **THEN** a retry or operation-feed reload finds the original baseline, Quote, and target
- **AND** it cannot create a second Quote-linked operation or lose the provider side effect

#### Scenario: Client and authoritative Quote disagree

- **WHEN** a checkout request's target differs from the owner-authorized PuppyPay Quote
- **THEN** PuppyOne rejects the request before invoking checkout
- **AND** no client-supplied target becomes commercial authority

#### Scenario: Membership seat quote requires checkout

- **WHEN** an existing member-activation operation receives a Quote whose application mode is
  checkout
- **THEN** checkout advances that same member-activation operation
- **AND** the system does not create a second operation for the Quote

#### Scenario: Checkout webhook arrives after the HTTP response

- **WHEN** PuppyOne has stored a submitted checkout operation and accepts a newer entitlement
  revision carrying its Quote identity
- **THEN** the operation is atomically marked succeeded with that confirmed revision
- **AND** Desktop stops watching only after reading the terminal server state

#### Scenario: An uncorrelated revision follows a correlated revision

- **WHEN** PuppyOne accepts a newer entitlement publication without a Quote identity
- **THEN** it clears the previous revision's Quote correlation before storing the new revision
- **AND** the newer revision cannot accidentally confirm the previous Quote's operation

#### Scenario: Checkout webhook wins the response race

- **WHEN** the entitlement revision is accepted after the intent commit but before PuppyOne stores
  the checkout response
- **THEN** atomic confirmation or operation reconciliation confirms the existing operation from
  the entitlement projection
- **AND** the operation cannot remain falsely pending

#### Scenario: Recoverable worker failure is listed

- **WHEN** a durable provisioning operation has a retryable failed storage status
- **THEN** the BFF reports `retryable_failed`, `terminal=false`, and `retryable=true`
- **AND** Desktop does not hide it as a completed failure

### Requirement: Narrow organization billing access context

PuppyOne MUST expose the current authenticated member's organization role and billing-management
capability without requiring clients to enumerate all organization members. Desktop organization
preferences MUST be isolated by Cloud origin and user and validated against the latest membership
list.

#### Scenario: Billing page checks authority

- **WHEN** Desktop opens Billing for a selected organization
- **THEN** it reads only that user's organization access context before loading owner-only billing
- **AND** it does not fetch the Team member directory, seat usage, or Team entitlement view

#### Scenario: Application restarts for the same account and host

- **WHEN** the same user restarts Desktop and still belongs to the previously selected organization
- **THEN** that selection is restored
- **AND** a removed or foreign organization identifier is discarded after list validation

### Requirement: Resumable client observation

Desktop MUST observe a concrete durable operation rather than infer payment completion only from a
target summary. Polling MUST back off to a bounded interval, refresh immediately after application
focus, survive a Billing reload, and MUST NOT classify a slow browser checkout as failed merely
because a short client timer elapsed.

#### Scenario: User spends longer than one minute in checkout

- **WHEN** the provider checkout remains pending for longer than one minute
- **THEN** Desktop continues to display the durable pending operation without a false timeout
- **AND** paid access remains unchanged until the server reports success

#### Scenario: Account context changes while polling

- **WHEN** the user, Cloud host, session generation, or organization changes during a watch
- **THEN** the old watch is canceled and its result cannot update or open anything in the new
  context
