# Change: Personal credits for the local desktop Agent

## Why
The built-in desktop Agent cannot currently use an authenticated, paid official
model connection. Existing organization billing meters hosted Runtime, which is
outside the requested product scope.

## What Changes
- Add personal prepaid AI credits in PuppyPay and an authenticated inference BFF.
- Keep tool execution and the Agent loop in Desktop; forward only model requests.
- Add email sign-in, sandbox checkout, balance refresh and managed model selection.
- Meter actual provider cost with durable reservations, payment idempotency and
  interrupted-generation recovery. Keep hosted Runtime and cloud upload off.

## Impact
- New independent `MANAGED_AI_ENABLED` flag; no Cloud schema change.
- PuppyPay owns the new wallet tables and migration.
- Desktop owns a private, revocable loopback inference capability for its worker.
- Authorized by the user's September 21 request to implement and test the local
  built-in Agent payment loop in Polar sandbox. Sandbox pricing is provisional.
