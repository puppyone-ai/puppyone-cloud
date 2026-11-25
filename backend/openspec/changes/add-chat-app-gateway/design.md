# Design: Chat App Gateway

The canonical design is the ledger document
`puppy-issues:document/puppyone/architecture/09-chat-apps.md` (decisions, data,
flows, dispatch, relay, approvals, secrets, delivery guarantees, failures and
test contract). This file does not repeat it.

## Status
Confirmed with the product owner on 2026-11-25.

## Implementation notes
- Persistence: every state change goes through `chat_app_command` (people) or
  `chat_app_gateway_command` (processes); Python repositories are plain `def`.
- Leases and fences follow `src/platform/agent/runtime/worker.py`.
- Platforms implement `ports.ChatPlatform`; the gateway loop knows none of them.
