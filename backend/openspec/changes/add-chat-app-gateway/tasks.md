## 1. Data
- [x] 1.1 Additive migration: six tables, RLS, two command functions, indexes and retention
- [x] 1.2 Feature keys (shadow)
- [x] 1.3 Native PostgreSQL tests for the command functions

## 2. Gateway core
- [x] 2.1 Module, import layer, service role registration
- [x] 2.2 Webhook intake, dispatch, relay, gateway process
- [x] 2.3 Project routes, authorization manifest, public contract delta
- [x] 2.4 Generic secret encryption, configuration, fail-closed switch

## 3. Platforms
- [x] 3.1 Telegram (webhook secret, knock, guest mode, drafts, Stop)
- [x] 3.2 Slack (OAuth, signatures, events, streaming, uninstall)
- [x] 3.3 WeCom (long connection lease, code knock, streaming, rate limits)

## 4. Web
- [x] 4.1 `/open/projects/{id}` landing page

## 5. Verification
- [x] 5.1 Unit, contract, native and architecture tests
- [ ] 5.2 Local end-to-end with fake platforms (in progress)
