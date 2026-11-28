# Change: Ask the Cloud Agent from Telegram, Slack and WeCom chats

## Why
Project managers want the project's Cloud Agent where their team already talks.
The Agent runtime already executes, bills, approves and records Runs; what is
missing is a layer that receives chat messages, decides whether to act, submits
an ordinary Run through `AgentService.submit`, and relays its progress back.

## What Changes
- New `chat_gateway` service role (`python -m src.platform.chat_apps.gateway`).
- New business module `src.platform.chat_apps` at the top business layer.
- Six additive tables (`chat_app_*`), two command functions, RLS service-only.
  No existing table, view or function changes.
- Project routes under `/api/v1/projects/{project_id}/chat-apps` (manifested)
  and signed platform callbacks under `/api/v1/chat-apps/*`.
- Generic `encrypt_secret` / `decrypt_secret` in `src.infra.security.crypto`
  (algorithm, envelope and existing keys unchanged).
- Feature keys `chat_app.telegram|slack|wecom` (shadow) and the
  `CHAT_APPS_PLATFORMS` switch (empty = off: callbacks 404, gateway idle).
- Web: `/open/projects/{id}` landing page.

## Not in v1
- Mapping chat accounts to PuppyOne accounts ("people in the project may
  ask", the `/chat-apps/link` page). "Who can ask" is the owner or anyone in
  the chat; the owner is the chat account that brought the chat in with its
  code (on Slack: the person who installed PuppyOne there).
- Chat management rows in project activity.

## Impact
- Database phase: Expand only. Rollback = clear `CHAT_APPS_PLATFORMS`.
- Deployment: one new Railway service and environment variables (operator action,
  listed in the PR checklist; not part of this change's code).
- Agent runtime, billing, approvals and history are unchanged.
- Canonical design: `puppy-issues:document/puppyone/architecture/09-chat-apps.md`;
  Desktop: `puppy-issues:document/puppy-one-rust/cloud/projects/chat-apps.md`.
