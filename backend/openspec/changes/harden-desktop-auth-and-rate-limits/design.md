# Design

Desktop sign-in uses OAuth PKCE. Start stores a verifier and the allowlisted application callback under an opaque state. The provider returns to the backend, which atomically consumes state, exchanges the provider code, and stores the resulting session under a second opaque one-time code. The desktop then atomically consumes that code. Redis `GETDEL` supplies the cluster-wide replay boundary and Redis TTL supplies expiry.

Both authentication throttles use an atomic Redis Lua counter before any Supabase operation. Redis absence or failure returns 503 instead of silently presenting a per-process limiter as a security control.

