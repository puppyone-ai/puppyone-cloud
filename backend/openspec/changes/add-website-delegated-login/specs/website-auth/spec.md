## ADDED Requirements

### Requirement: Single sign-in surface
The PuppyOne website SHALL sign in only through Cloud `/login` and SHALL NOT present its own credential form.

#### Scenario: Website user signs in
- **WHEN** a website page needs an account
- **THEN** it opens Cloud `/login?client=website` with its own state and S256 challenge

### Requirement: Website-owned verifier and session
The website SHALL own the PKCE verifier and its Supabase session; Cloud SHALL NOT redeem website codes or write Web cookies for it.

#### Scenario: OAuth through the shared page
- **WHEN** a provider returns to Cloud `/auth/callback` with website parameters
- **THEN** Cloud relays only code and state to the allowlisted website callback without exchanging the code

#### Scenario: Email through the shared page
- **WHEN** a user signs in with email on a website intent
- **THEN** an isolated temporary session is handed to the website by top-level form POST and cleared, and the Cloud Web session is untouched

### Requirement: Exact website receivers
Cloud SHALL deliver website sign-in results only to `<origin>/auth/callback` or `<origin>/auth/session` for origins configured for that environment.

#### Scenario: Unlisted receiver
- **WHEN** a login or callback names a website origin that is not configured
- **THEN** sign-in is refused and nothing is relayed
