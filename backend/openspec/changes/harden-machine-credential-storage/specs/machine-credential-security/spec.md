## ADDED Requirements

### Requirement: Dedicated credential hashing secret
The system MUST hash machine credentials only with the dedicated access
credential hashing secret and MUST fail closed when it is unavailable.

#### Scenario: Production secret is missing
- **WHEN** a non-development deployment starts without the dedicated secret
- **THEN** startup fails before a machine credential can be issued or verified

### Requirement: Credential-free durable configuration
The system MUST NOT store newly issued agent or sandbox bearer credentials in
access-surface configuration and MUST only return plaintext at issuance time.

#### Scenario: Ordinary read after issuance
- **WHEN** a client reads or updates an existing agent or sandbox surface
- **THEN** the response contains credential metadata but no replayable secret

### Requirement: Hash-first authentication and revocation
The system MUST authenticate current credentials by hash and MUST reject a
revoked credential on the next authenticated operation.

#### Scenario: Credential rotation
- **WHEN** a surface credential is regenerated
- **THEN** the previous hash is revoked, the new plaintext is returned once, and the previous token no longer authenticates

### Requirement: Bounded legacy compatibility
The system MAY authenticate an unmigrated legacy plaintext credential only when
the corresponding hashed credential state is absent.

#### Scenario: New scope row cannot use plaintext fallback
- **WHEN** a scope row has an access-key hash
- **THEN** plaintext lookup is not an eligible authentication path for that row

