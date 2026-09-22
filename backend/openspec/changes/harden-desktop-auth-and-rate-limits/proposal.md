# Change: Harden desktop OAuth and authentication throttling

## Why

Desktop OAuth and authentication throttles must remain correct across replicas. Process-local maps cannot provide durable TTLs or atomic one-time consumption, and post-authentication throttling does not stop password guesses.

## What Changes

- Add a Redis-backed atomic TTL store for desktop OAuth state and exchange codes.
- Implement PKCE desktop OAuth callback/exchange without storing bearer sessions in process memory.
- Move login and check-email throttling to a shared Redis limiter that runs before upstream auth calls.
- Fail closed when the shared security store is unavailable.

## Impact

- Affected specs: desktop-auth, authentication-rate-limits
- Affected code: `src/platform/auth`, authentication settings and tests

