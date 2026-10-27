## ADDED Requirements
### Requirement: Supabase-owned client authentication
Web and Desktop SHALL authenticate through Supabase without Puppyone Redis or server-owned handoff state. Desktop SHALL own its PKCE verifier and keep refresh credentials in system secure storage. Web and Desktop SHALL use independent sessions.

#### Scenario: Redis is unavailable
- **WHEN** the authentication Redis configuration is absent or unreachable
- **THEN** login, callback exchange and refresh continue through Supabase

#### Scenario: Desktop callback replay
- **WHEN** a code, state or callback does not match the active unexpired native PKCE attempt
- **THEN** Desktop rejects it without adopting a session

#### Scenario: Web logout
- **WHEN** Web revokes its own session
- **THEN** the independent Desktop session is not revoked
