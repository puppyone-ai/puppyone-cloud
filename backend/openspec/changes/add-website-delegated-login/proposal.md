# Change: Let the PuppyOne website sign in through Cloud /login

## Why

The marketing website (`puppyone-web`) shipped its own login form that called
Supabase directly with the implicit flow, so tokens returned in page URLs and
the product had two sign-in UIs. The approved architecture keeps one sign-in
page, owned by Cloud Web, while each client owns its own session.

## What Changes

- Add a `website` delegated login intent beside `desktop`: the existing
  `/login` page, layout and email-first interaction serve the website too.
- The website owns its PKCE verifier and session. Cloud authorizes OAuth with
  the website's S256 challenge and `/auth/callback` only relays code + state
  to an allowlisted `<website origin>/auth/callback`.
- Email sign-in uses the same isolated sessionStorage context as Desktop and
  hands the temporary refresh token to `<website origin>/auth/session` by
  top-level form POST; the website rotates it directly with Supabase.
- Allowed website origins are explicit per environment
  (`NEXT_PUBLIC_AUTH_WEBSITE_ORIGINS`); empty disables website sign-in.
- Desktop and website share one delegated-client implementation (relay,
  temporary context, session hand-off) with client-specific validators.

## Approval and authority

The user approved this architecture on 2026-10-27 (Cloud /login is the only
sign-in UI; the website becomes a delegated client like Desktop).
Canonical design: `puppy-issues:document/puppyone/architecture/control-plane/authentication-and-sessions.md`.

## Impact

- Affected capability: website-auth (new); desktop-auth behavior is unchanged.
- Affected code: Cloud frontend auth (`features/auth/`, `app/auth/callback`,
  `app/login`, middleware), frontend build arguments.
- No backend, database or Supabase redirect-allowlist change: the website
  never receives a Supabase redirect directly.
