## 1. Implementation

- [x] 1.1 Add injectable Redis atomic TTL store.
- [x] 1.2 Add PKCE desktop start/callback/exchange endpoints with callback allowlisting.
- [x] 1.3 Replace process-local auth counters with preflight shared throttling.
- [x] 1.4 Add hosted configuration validation.

## 2. Verification

- [x] 2.1 Test cross-instance OAuth completion, expiry and replay.
- [x] 2.2 Test login is rejected before Supabase and check-email returns standard 429.
