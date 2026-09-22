# PuppyOne Architecture Vision

PuppyOne is a Git-native cloud filesystem for AI agents, humans, and
connectors. The product keeps GitHub-like collaboration semantics while adding
server-owned scope boundaries, audit, conflict policy, search/indexing, and
agent-friendly filesystem entry points.

## Product Goals

- Agents and humans can use ordinary files as the shared context surface.
- Native Git clients can clone, fetch, push, diff, and inspect history.
- Web/API/Puppyone CLI writes have the same versioning, audit, conflict, and
  scope semantics as Git pushes.
- Large repositories avoid full-repo cloning on product saves.
- Concurrent writers are resolved through server-side policy rather than
  hidden last-writer side effects.

## Current Invariants

1. Git owns version facts: blobs, trees, commits, refs, and pack transport.
2. PuppyOne owns collaboration semantics: scopes, access points, auth,
   excludes, conflict policy, audit, projection, outbox, and indexing.
3. `VersionWriteEngine` is the only publish authority.
4. Product/Web/API writes default to the Project-root projection and create one user-visible
   history event.
5. Access Point Git writes use scoped repo facades over a shared project object
   store, not one physical repo per scope.
6. Search, notifications, projections, and repair work are derived consumers of
   accepted write events.

## High-Level Flow

```text
Web / upload / connector / Puppyone CLI
        |
        v
ProductOperationAdapter
        |
        v
VersionWriteEngine
        |
        +--> Git object storage
        +--> Supabase refs/history/audit/transactions/outbox

Stock Git client
        |
        v
Git smart HTTP adapter
        |
        v
VersionWriteEngine
```

See [01-version-engine.md](01-version-engine.md) for the complete current
architecture.
