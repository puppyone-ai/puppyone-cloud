# Design: live workspace file write path

## Response path

```text
today (warm write)
  activity fence ─► billing check ─► intent audit ─► sandbox write ─► completion audit ─► final authorization ─► reply

Part A
  ┌ activity fence ┐
  ├ billing check  ├─► sandbox write ─► final authorization ─► reply
  └ intent audit   ┘                  └► completion audit (owned, off the reply)

Part A + B (mutations)
  ┌ activity fence ┐
  ├ billing check  ├─► sandbox write ─► reply
  └ intent audit   ┘                  └► completion audit (owned, off the reply)
```

## Invariants
- No file channel is used before the SQL revision fence and the billing check pass.
- The intent row exists before the mutation is sent to the sandbox.
- Every intent is followed by `rejected` when admission or the operation is refused,
  or by `completed` when it finished; a missing follow-up still means unknown outcome.
- The intent is written early only for a warm snapshot that already passed the
  canonical Python policy for that user, project and action. An unauthenticated or
  unauthorized caller cannot create audit rows in another project.
- Reads return bytes only after final authorization (unchanged).
- A mutation reply is a receipt for a mutation admitted under the current fence;
  it carries no bytes the caller did not send.

## Background audit ownership
The service keeps a set of in-flight audit tasks created with `run_blocking`.
`close()` awaits them (bounded) before closing channels. A failed insert is logged
with the operation id; it never changes the reply that was already sent.

## Failure cases
| Case | Result |
| --- | --- |
| Fence rejects after an early intent | `rejected` row, 409/403 reply, no file access |
| Billing reservation expired | same as today: fail closed before file access |
| Sandbox refuses (conflict, missing file) | `rejected` row (background), mapped error reply |
| Transport lost mid-operation | intent only: unknown outcome, error reply (unchanged) |
| Process stops before the completion insert | intent only: unknown outcome |
| Authority revoked during a mutation (Part B) | the admitted mutation's receipt is returned; the next request is refused |
| Generation replaced during a mutation (Part B) | the receipt names the admitted generation; the next request fails with `workbench_generation_changed` |

## Verification
- Unit tests: ordering (intent before operation), concurrency of fence/billing/intent,
  rejected closing an early intent, cold path order, background completion and
  shutdown drain, reads still re-authorize, mutations skip it only with Part B.
- Stage timings for both audit inserts; repeat the 8-sample local measurement.
