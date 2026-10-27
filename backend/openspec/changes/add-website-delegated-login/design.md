# Implementation design

The canonical architecture is owned by the approved Puppyone documentation,
not duplicated here. Desktop and the website are both delegated clients
(`features/auth/delegated-client.ts`): they own the PKCE verifier and their
session, reuse `/login`, and never read or write Cloud Web cookies.

Only the receiver differs. Desktop accepts the exact per-attempt
`http://127.0.0.1:<port>/auth/callback`; the website accepts exactly
`<origin>/auth/callback` for an origin listed in
`NEXT_PUBLIC_AUTH_WEBSITE_ORIGINS` (HTTPS, or loopback HTTP for development).
The relay forwards only `state` and `code` (or a bounded `access_denied`), and
a callback carrying both Desktop and website parameters is rejected.

Delegated login pages send `Referrer-Policy: origin` so the receiver can verify
the cross-origin form POST Origin; middleware never touches the Web session for
them. The website side (state + verifier in a `__Host-` HttpOnly cookie, code
redemption, refresh-token rotation, idempotent `/api/v1/auth/initialize`) is
implemented in `puppyone-web`.
