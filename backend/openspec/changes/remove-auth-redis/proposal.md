# Change: Use Supabase native authentication without Redis

## Why
Desktop authentication currently fails before reaching Supabase when its unrelated Redis dependency is unavailable. The user approved removing the authentication Redis dependency and using native Supabase authentication on both clients.

## What Changes
- **BREAKING** Retire server-owned Desktop handoff and anonymous email enumeration routes.
- Desktop owns PKCE and directly exchanges and refreshes Supabase credentials; Web retains native SDK authentication.
- Remove authentication Redis storage, configuration and startup requirements; preserve Redis used by other domains.

## Impact
- Affected specs: auth
- Affected code: platform/auth, Web auth, Desktop Main authentication, configuration and tests.
