# Implementation design

The canonical architecture is owned by the approved Puppyone documentation,
not duplicated here. Desktop browser authentication uses a per-attempt
sessionStorage-backed Supabase PKCE client with automatic refresh disabled.
The fixed Web callback restores that same browser context before code exchange;
normal Web SSR cookies are neither read nor replaced by a Desktop attempt.
Backend session validation checks the access/refresh pair before issuing a
native exchange code. Redis compare-and-transition operations preserve valid
requests on invalid proofs and guarantee a single successful consumer.

Compatibility preserves existing native request/response shapes and short-lived
legacy pending callbacks. New provider hints use the unified Web login path.
