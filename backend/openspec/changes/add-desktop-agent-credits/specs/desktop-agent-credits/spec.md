## ADDED Requirements

### Requirement: Personal paid inference
The service SHALL authorize inference using the authenticated human's personal
credit balance and SHALL NOT require or create a hosted Runtime or project.

#### Scenario: Insufficient balance
- **WHEN** a signed-in desktop user cannot cover a request reservation
- **THEN** return HTTP 402 before any model invocation
- **AND** offer a personal credit checkout

### Requirement: Authoritative settlement
The service SHALL reserve before execution, admit a request identity once, and
settle from provider usage under a unique ledger reference.

#### Scenario: Interrupted stream
- **WHEN** the desktop disconnects after the provider starts generating
- **THEN** retain the reservation and recover usage from the stored generation ID
- **AND** do not grant free usage or charge twice

### Requirement: Sandbox payment integrity
The service SHALL grant credit only from a verified paid event matching a stored
purchase, and SHALL apply cumulative refunds idempotently.

#### Scenario: Duplicate paid event
- **WHEN** Polar repeats a paid webhook for the same purchase
- **THEN** the personal account receives credit exactly once
- **AND** hosted Runtime balances and organization entitlements do not change
