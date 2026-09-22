## ADDED Requirements

### Requirement: Supabase-compatible schema history
The system MUST keep every shared database schema change in the ordered
`supabase/migrations` history and MUST NOT represent external application code
as an implicit step inside that history.

#### Scenario: Schema deployment
- **WHEN** CI deploys a schema release
- **THEN** it applies only version-controlled Supabase migration files
- **AND** no migration comment or workflow step is required to execute hidden application code between files

### Requirement: Portable data migration artifacts
The system MUST represent each non-transactional, batched, secret-dependent, or
application-language data migration as an immutable manifest-driven artifact
under `supabase/data_migrations` that can run without GitHub Actions.

#### Scenario: Self-hosted execution
- **WHEN** a self-hosted operator selects a data migration with the PuppyOne CLI
- **THEN** the same manifest, entrypoint, prerequisite checks, and verification used by hosted CI are executed

### Requirement: Durable environment-local completion receipt
The system MUST write a completion receipt in the target database only after
the selected artifact succeeds and its verification passes.

#### Scenario: Failed migration
- **WHEN** execution or verification fails
- **THEN** no successful receipt is published
- **AND** an idempotent retry remains eligible

#### Scenario: Released artifact changes
- **WHEN** a database receipt exists for an ID whose current artifact checksum differs
- **THEN** the runner refuses execution and reports an immutable-history violation

### Requirement: Explicit schema-data ordering
The system MUST verify every data migration's prerequisite schema versions and
MUST keep destructive contract changes separate from expand and data phases.

#### Scenario: Missing prerequisite
- **WHEN** a data migration is selected before a required schema version exists
- **THEN** the runner fails before changing data and identifies the missing version

#### Scenario: Contract before data completion
- **WHEN** legacy rows remain and the required data migration has no successful receipt
- **THEN** the contract migration fails without dropping the legacy structure

#### Scenario: Reviewed contract changes during promotion
- **WHEN** a promoted Contract differs from the released `contract.pending.sql`
- **THEN** repository policy rejects it even when its data checksum marker is valid

#### Scenario: Fresh installation
- **WHEN** a fresh database contains no legacy rows
- **THEN** it can reach the final schema without executing an irrelevant historical data migration

### Requirement: Protected environment promotion
The system MUST execute Qubits and Production migrations through the same
implementation with separate protected secrets and serialized database jobs.

#### Scenario: Production data migration
- **WHEN** an operator dispatches a Production data migration
- **THEN** GitHub validates the protected branch and environment, verifies the same artifact and postcondition in Staging, runs the portable runner at an exact source SHA, and prevents another Production database job from running concurrently

#### Scenario: Pull request without database changes
- **WHEN** a pull request changes no database release path
- **THEN** CI publishes a successful stable database validation result without receiving shared-database credentials or running the expensive database suite

### Requirement: Applied history remains immutable
The system MUST treat migrations already applied to any shared database as
immutable and MUST correct defects with forward artifacts.

#### Scenario: Legacy July migration
- **WHEN** the new framework adopts a backfill associated with an already-applied July migration
- **THEN** it checksum-pins and preserves the original SQL history and marks the data artifact as legacy compatibility
