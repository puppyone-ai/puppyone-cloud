## MODIFIED Requirements

### Requirement: Protected environment promotion
The system MUST execute schema and data migrations through separate reusable
implementations with protected, environment-specific secrets and serialized
database jobs. The protected-environment release orchestrator MUST invoke both
lanes automatically for each protected-branch source SHA in schema → data
plan/run/verify → final-schema order. Production data execution MUST first
verify the same artifact checksum and row-level postcondition in Qubits.

#### Scenario: Automatic Qubits release
- **WHEN** a commit is pushed to the protected `qubits` branch
- **THEN** CI resolves the staged immutable data-migration pointer, deploys its
  prerequisite schema, plans, runs and verifies the artifact, and performs a
  final schema deployment check before the source SHA is eligible for
  application deployment

#### Scenario: Automatic Production release
- **WHEN** a commit is pushed to the protected `main` branch with a promoted
  data-migration pointer
- **THEN** CI verifies the exact artifact in Qubits before it writes to
  Production, then runs and verifies Production through the protected
  Production environment

#### Scenario: Incomplete data release
- **WHEN** an artifact has missing environment secrets, a missing prerequisite,
  or fails verification
- **THEN** the orchestrator fails the protected-branch workflow and no
  application deployment for that source SHA is eligible to proceed

#### Scenario: Operator recovery
- **WHEN** an operator invokes the standalone data-migration workflow
- **THEN** it remains restricted to protected branches and environments and can
  perform diagnosis or an idempotent recovery without weakening the automatic
  release ordering
