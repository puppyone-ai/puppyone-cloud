## ADDED Requirements

### Requirement: Hosted runtime reservation and settlement

Every hosted compute entry point MUST reserve runtime units before starting work, heartbeat a long
lease, and settle actual usage exactly once across success, failure, cancellation, timeout, and
worker recovery.

#### Scenario: Runtime credit is insufficient

- **WHEN** a required-mode hosted run cannot reserve its upper-bound runtime units
- **THEN** PuppyOne does not start the compute workload
- **AND** returns a structured quota error

#### Scenario: Worker crashes after reserve

- **WHEN** a worker crashes after obtaining a reservation
- **THEN** recovery finds the durable run record
- **AND** expires or settles the reservation idempotently without a duplicate debit

### Requirement: Unified logical storage quota

All product mutations that increase logical active storage MUST pass one quota boundary and update
a reconcilable organization counter. Over-quota organizations MUST retain delete, export, and
usage-reducing operations.

#### Scenario: Write crosses the quota

- **WHEN** a write would make logical active storage exceed the effective limit
- **THEN** the mutation is rejected before publication
- **AND** existing content remains readable and deletable

#### Scenario: Counter drift is found

- **WHEN** a periodic full scan differs from the incremental counter
- **THEN** the system records the drift and safely corrects the counter
- **AND** emits threshold/reconciliation telemetry

### Requirement: Progressive enforcement

Seat billing, runtime metering, and storage enforcement MUST support independent
`disabled`, `shadow`, and `required` modes.

#### Scenario: Shadow mode observes a denial

- **WHEN** shadow evaluation would deny an operation
- **THEN** the operation follows the pre-cutover behavior
- **AND** the would-deny decision and expected financial/quota effect are recorded for comparison

#### Scenario: Required mode is enabled

- **WHEN** a subsystem is switched to required after parity verification
- **THEN** all registered entry points enforce its decision server-side
- **AND** unregistered or indeterminate variable-cost paths fail closed
