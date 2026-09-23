## Implementation
- [x] Extract and implement unified frontend authentication and callback ownership.
- [x] Implement independent Desktop browser sessions and safe cleanup.
- [x] Extract Desktop API service, validate sessions and implement atomic state transitions.
- [x] Add real page/HTTP/Redis regression coverage and run release checks.
- [x] Verify the real email flow against test Supabase and record third-party/platform limits.
- [x] Update canonical documentation with implementation and verification evidence.

## Release acceptance (separate from implementation)

- Real Google/GitHub authorization and Mac/Windows full system-browser acceptance remain release gates; staging only enables email.
- Production rollout is not included in the local verification receipt.
