# Design

Fresh HTTP authorization and admitted repository snapshots remain mandatory. A cache hit never grants access. The snapshot's graph reachability, type/hash checks, live pin and generation protect every physical read. Normal read adapters use stable immutable pack identities; fresh publication proof continues to use a distinct uncached readback attempt.

History summary cache entries contain only immutable graph values, not storage readers, pins or authorization grants. Its key includes all refs and repository generation/ref sequence. Builders own their read pin until completion; one disconnected subscriber cannot cancel another, and all departed subscribers stop cooperative work. All worker lifetimes are joined.

List queries omit tree diffs with explicit changes_loaded=false. Existing clients default to full details. Selected details use a fresh admitted snapshot and reject commits unreachable from current refs. Frontend cache identity includes API endpoint, account/session, project, revision and cursor. Cached rows may paint during revalidation; authorization failure clears them. Last-subscriber cancellation crosses the secure IPC boundary and aborts only sender-owned GETs.

Bulk placement metadata uses keyset pages guarded by read pin, generation and GC epoch. Pages are held in RAM one at a time, indexed in a private temporary SQLite file with a bounded page cache, and deleted before pin release. Small file reads retain lazy placement lookup and do not enumerate the repository. Application code requires the additive SQL function before local activation. No data migration or new durable table is involved.

Warm-history budgets are fresh authorization + admitted refs + release, with zero object downloads. Cold history uses paged metadata and each distinct retained pack once; cache eviction can require a later download. Loose objects still require physical GETs on a cold graph build. Cross-replica caches are independent and all caches are disposable.
