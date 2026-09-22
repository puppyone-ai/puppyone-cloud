## ADDED Requirements

### Requirement: Sensitive authentication requests are globally throttled

The service SHALL apply a shared atomic limit to login and check-email before contacting the authentication provider and SHALL return `429` with `Retry-After` when exceeded.

#### Scenario: Login threshold exceeded

- **WHEN** a login bucket has reached its global threshold
- **THEN** the request is rejected before Supabase authentication is called

