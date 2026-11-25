## 1. Data
- [ ] 1.1 Additive migration: eight tables, RLS, two command functions, activity view kind
- [ ] 1.2 Feature keys (shadow)
- [ ] 1.3 Native PostgreSQL tests for the command functions

## 2. Gateway core
- [ ] 2.1 Module, import layer, service role registration
- [ ] 2.2 Webhook intake, dispatch, relay, gateway process
- [ ] 2.3 Project routes, authorization manifest, public contract delta
- [ ] 2.4 Generic secret encryption, configuration, fail-closed switch

## 3. Platforms
- [ ] 3.1 Telegram (webhook secret, knock, guest mode, drafts, Stop)
- [ ] 3.2 Slack (OAuth, signatures, events, streaming, uninstall)
- [ ] 3.3 WeCom (long connection lease, code knock, streaming, rate limits)

## 4. Web
- [ ] 4.1 `/open/projects/{id}` landing page and `/chat-apps/link`
- [ ] 4.2 `chat_app` activity kind

## 5. Verification
- [ ] 5.1 Unit, contract, native and architecture tests
- [ ] 5.2 Local end-to-end with fake platforms
