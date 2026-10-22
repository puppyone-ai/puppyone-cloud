# Capacity transaction boundary

The existing 4 MiB / 16,384-entry metadata page is the unit of admission, quota
allocation and rollback. PostgreSQL owns this boundary; Python's capacity and
storage adapters only prepare bounded data and invoke it.

The page RPC keeps Organization -> Project -> repository/pin -> capacity lock
ordering, validates the live pin/epoch, then explicitly admits the current actor,
lease and file policy once. Membership, credential, surface, lease and entitlement
rows remain locked until the short transaction ends. The canonical Runtime
resolver already evaluates the user's Project role, so its caller must not
independently resolve the same role a second time.

The admission context is a PL/pgSQL local variable, never supplied by a client,
stored in a session setting or persisted for reuse. It contains the file limit,
locked credential/entitlement expiries, and bound lease identity. Before returning,
the transaction rechecks wall-clock expiries and the existing lease/Agent fence.
It does not resolve a normal Git actor again for every object or at transaction
exit. A later page/transaction must obtain new admission. Final ref publication
still checks current permissions and expected refs independently.

Batch file-size validation checks only newly admitted blobs, preserving
grandfathered existing objects. The 64 MiB bare-repository object guard remains
for all writers as a statement trigger using the inserted row set. The page RPC
also preserves the previous guard for conflict inputs when inserting a mixed
old/new batch. Foreign keys, uniqueness and nonnegative counters are unchanged.

The compatibility RPC preserves its default I/O identity, 200-entry bound and
error contract, and delegates to the page RPC. Applications cannot INSERT/UPDATE
capacity tables or call private helpers. The only other insert owners are the
two owner-only inventory activation functions (before file-policy enrollment)
and verified recovery archive finalization (copying its exact verified inventory).
They remain inaccessible to service_role; no new bypass is exposed.

Both trigger removal and replacement functions ship in one transactional SQL
migration. Existing rows are not rewritten. A failed migration restores old
functions/triggers. Old application callers work after upgrade. No DB transaction
or row lock spans S3 transfer. Rollback after successful deployment requires a
new forward migration, not editing applied history.

Performance regression checks count functions for 1 and 6,017 objects with real
PostgreSQL and an enrolled Runtime credential. Standalone timing artifacts label
CPU/DB-only scope and never claim end-to-end or hosted performance.
