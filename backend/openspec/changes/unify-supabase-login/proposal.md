# Change: Unify Supabase login and Desktop session handoff

## Why

Desktop social-login buttons call an unregistered endpoint. Browser and native
clients also copy one refresh token instead of owning independent sessions.

## What Changes

- Use one Supabase login coordinator for email and social authentication.
- Preserve Web sessions while Desktop uses an isolated temporary auth context.
- Separate Desktop HTTP, application and shared atomic storage responsibilities.
- Verify session pairing, bind browser attempts and atomically validate before consumption.
- Exercise real page actions, callback routes and HTTP contracts in tests.

## Approval and authority

The user approved implementation of the documented architecture on 2026-09-23.
Canonical design: `puppy-issues@1a4cf83:document/puppyone/architecture/control-plane/authentication-and-sessions.md`.
This change implements that approval; no further proposal approval is pending.

## Impact

- Affected capability: desktop-auth; preserves native start/complete/exchange URLs.
- Affected code: Cloud frontend auth, Cloud auth API and Redis, authentication tests.
- No payment schema changes or login-page visual redesign.
