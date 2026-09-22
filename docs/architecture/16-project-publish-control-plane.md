# Project Publish Control Plane

This document defines how a local Git repository becomes a hosted PuppyOne
Project without changing the Version Engine architecture. Project publication
is split between a transactional control plane and the existing Git-native data
plane.

## Layering

```text
Organization                         tenant, membership, billing, quota
└── Project                          ownership and human authorization
    ├── Publish control-plane facts  idempotency and deletion lifecycle
    ├── Git Access Surface           credential and RuntimeGrant boundary
    └── Canonical Repository         frozen Version Engine authority
        ├── Project-root view        /git/{project_id}.git
        └── optional Scope views     /git/{project_id}/scopes/{scope_id}.git
```

The control plane may create, authorize, tombstone, and schedule cleanup for a
Project. Only the Version Engine admits Git content, publishes refs and root
state, records version history/audit, and owns object storage semantics.

## Idempotent Project admission

Desktop Project creation requires an explicit `org_id` and a canonical UUID v4
`Idempotency-Key`. The durable namespace is:

```text
(authenticated_user_id, operation_kind, idempotency_key)
```

The source-independent request fingerprint contains the explicit Organization,
requested name and description, publication mode, and workflow request facts.
Resolved mutable source facts are stored separately as result metadata. Within
the admission transaction the server:

1. serializes contenders for the key;
2. returns a same-request replay before evaluating quota;
3. rejects a different request for the same key;
4. rejects replay when the original Project is tombstoned;
5. verifies current membership in the explicit Organization;
6. serializes Organization quota admission;
7. allocates a collision-free default display name while holding the
   Organization lock;
8. creates a hidden `initializing` Project and its creator Admin fact; and
9. records the durable operation, source/result metadata, deadline, and retry
   state.

This ordering prevents both duplicate Projects after response loss and quota
oversubscription under concurrent creates. It does not create Git commits or
simulate a Push.

Root initialization is deliberately outside that transaction and remains owned
by the existing L5 API:

```text
prepare transaction
  -> hidden Project(initializing) + creator Admin + operation
  -> renewable initialization write lease
  -> VersionWriteEngine.initialize_project_tree(project_id)
  -> optional single-owner content initializer
  -> completion transaction verifies canonical root
  -> Project(ready) becomes product-visible
```

`empty` initialization is idempotently resumable. Contentful template and
landing workflows use `deferred` publication and an at-most-once initializer
attempt; they never publish an empty substitute after failure. Deadline or
retry exhaustion first attempts a safe abort. If fail-closed validation cannot
prove deletion safe, the hidden operation becomes a durable, observable
dead-letter instead of retrying forever or exposing a partial Project.

Replay checks the durable operation before accessing a Registry, resolving a
`latest` alias, checking landing-ticket expiry, or downloading a preview
object. Consequently a lost successful response remains replayable after those
sources change or disappear. Every replay re-evaluates current human authority
before returning Project data or reconstructing a deterministic operation
secret.

Template provisioning remains a distinct workflow. A plain Desktop publish
cannot silently switch to a template or seed path.

## Operation credential

Credential issuance uses the same client operation UUID in its own endpoint
namespace. Electron main supplies a high-entropy secret over TLS; the service
stores only its keyed hash and redacted hints. Same key and same canonical
payload replay the same credential identity. Key reuse with a different target,
mode, or secret hash fails closed.

The credential is a normal exact-target Git runtime principal. It is not a
checkout Binding and carries no device, path, worktree, or client identity.
Current Project membership and role still cap its effective `RuntimeGrant` on
every Git request.

## First Push

The newly created Project is Git-visible as empty. Desktop pushes an immutable
local commit SHA through the existing canonical Project-root smart-HTTP route.
All receive-pack policy, quarantine, CAS, root publication, audit, history, and
derived follow-up remain unchanged inside the Version Engine.

Control-plane success never implies content success. After an uncertain
transport response, Desktop compares the canonical remote ref with the expected
SHA. Only the Version Engine's accepted ref establishes that the Project has
been published.

## Abandon and deletion

Initialization Abandon is limited to the actor and operation that created the
Project. It is allowed only while the canonical repository remains empty and no
accepted root Push/head or unexpected bootstrap side effect exists. It does not
delete the Project row inline. Abandon, deferred abort, and ordinary deletion
all enter the same durable lifecycle:

```text
ready | initializing
  -> deleting                 close new write admission
  -> drain                    wait for renewable writer leases to end/expire
  -> snapshot cleanup facts   while relational ownership still exists
  -> remove relational aggregate
  -> purge                    retry idempotent owned-resource cleanup
  -> verify                   quiet-window check; late activity returns to purge
  -> completed
```

Write leases live outside the Version Engine and cover the real physical I/O
lifetime, including cancellation-resistant storage and external-provider
calls. Initialization obtains a narrowly proven lease tied to its operation;
ordinary writers may acquire only for a `ready` Project. Deletion changes
lifecycle before draining, so no new Git, Product API, ingest, index, sandbox,
or background writer can start after the linearization point.

The deletion job is a durable, self-contained manifest. At minimum its S3
closure contains:

```text
version/{project_id}/
projects/{project_id}/
shadow-snapshots/{project_id}/
users/{principal}/etl_artifacts/{project_id}/
users/{principal}/processed/{project_id}/
users/{principal}/raw/{project_id}/
```

The principal ledger is refreshed after write drain and before relational
cascade. Cleanup deletes objects, aborts incomplete multipart uploads, verifies
the exact prefixes are quiet, and restarts purge if either late objects or late
multipart uploads appear. External search namespaces, hosted sandbox handles,
and other PuppyOne-owned derived resources follow the same snapshot,
destroy/retry, and verify contract. User-owned export destinations are not
deleted, but an export must finish and release its write lease before drain may
complete.

The job is durably journaled, observable, idempotent, and retryable. It survives
Project-row deletion and invokes storage/provider interfaces from outside
`backend/src/version_engine/`; it does not teach the Version Engine about
Desktop operations. An Organization may be deleted only after it has no
Projects and every Project deletion job for that Organization is completed.

Historical user-prefix data must be inventoried into the principal ledger
before destructive admission is enabled. The rollout stays fail-closed until a
paginated object-and-multipart scan plus an independent verification pass marks
the inventory complete.

## Stable failure contract

| Condition | Result |
|---|---|
| missing idempotency key | typed 400 |
| malformed key | typed 422 |
| missing explicit Organization | typed 422 |
| same key, same payload | stable replay |
| same key, different payload | typed 409 |
| original target tombstoned | typed 410 |
| matching operation still initializing | typed 409, no source access |
| initialization dead-lettered | typed retryable 503, operator-visible |
| concurrent quota exhausted | typed quota rejection, no partial Project |
| accepted content exists during Abandon | typed 409, no deletion |
| deletion inventory not verified | typed retryable failure, no deletion |
| Organization cleanup still running | typed conflict, no Organization deletion |

Internal database exceptions and session-generation messages are never product
copy.

## Frozen Version Engine boundary

This control-plane feature must not modify:

- repository target geometry or root/Scope semantics;
- Git smart-HTTP protocol behavior;
- receive-pack policy, quarantine, or merge/conflict rules;
- CAS/root/ref/history/audit publication;
- object layout, storage abstraction, or GC semantics; or
- human `ProjectGrant` versus machine `RuntimeGrant` separation.

Cross-layer tests may exercise those public contracts. The implementation diff
must contain no source changes under `backend/src/version_engine/`.
