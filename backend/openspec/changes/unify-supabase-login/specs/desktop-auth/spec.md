## ADDED Requirements

### Requirement: Unified Supabase completion
All enabled authentication methods SHALL converge on one login-intent completion flow.

#### Scenario: Desktop chooses Google or GitHub
- **WHEN** a user chooses a provider on the Desktop browser login page
- **THEN** Supabase authorizes the provider and restores the same native request without an unregistered route

### Requirement: Independent client sessions
Web and Desktop SHALL own independent refresh-token sessions.

#### Scenario: Browser is already signed in
- **WHEN** Desktop starts authentication
- **THEN** the temporary Desktop client does not read, replace or revoke the existing Web session

### Requirement: Validated atomic handoff
The handoff SHALL verify session identity and native proofs before one-time consumption.

#### Scenario: Invalid proof precedes valid exchange
- **WHEN** an incorrect verifier is submitted before the matching verifier
- **THEN** the invalid attempt fails and at most one matching exchange succeeds

### Requirement: Honest verification evidence
Login acceptance SHALL exercise page actions and real registered HTTP routes.

#### Scenario: Test environment has no social provider
- **WHEN** CI uses a provider fixture and staging uses real email
- **THEN** results are recorded separately from real Google/GitHub and native platform acceptance
