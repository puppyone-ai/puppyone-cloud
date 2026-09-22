## Context

The Version Engine already treats `projects.version_root_hash` and the Project
object store as canonical. A root `repo_scopes` row is therefore a persistence
sentinel, not a second resource. It leaks through Scope list APIs, workspace
bindings, readiness, Git RuntimeGrant, Web state, and Desktop state.

## Goals

- Make Project existence imply repository-root identity.
- Make every persisted Scope a real non-empty path boundary.
- Keep exact Project/Scope relational integrity and fail-closed authorization.
- Preserve one object store, one canonical history, and path-bounded views.
- End with one schema and one API contract, without long-lived compatibility.

## Non-goals

- Multiple canonical repositories per Project.
- A generic `repository_targets` supertype without an independent lifecycle.
- A physical repository, object store, or human role per Scope.
- Changing canonical Git URLs, Git object encoding, or CAS algorithms.
- Retiring the bounded legacy `/git/ap/<secret>.git` route in this change.

## Decision 1: Project is the root target

The domain contract is a discriminated union:

```python
RepositoryTarget = ProjectRootTarget(project_id) | ScopeTarget(project_id, scope_id)

ResolvedRepositoryView(
    target,
    path_prefix,
    excludes,
    max_mode,
    ref,
)
```

Project root resolves to `path_prefix=""`, no excludes, and `max_mode="rw"`.
A Scope target resolves current path, excludes, and mode from
`repository_scopes`. Effective authorization remains the intersection of the
credential, binding, current human Project capability, and Scope maximum.

## Decision 2: Nullable child key, not a target table

`access_surfaces` and `project_workspace_bindings` keep `project_id NOT NULL`
and use `scope_id NULL` for Project root. A separate Project foreign key proves
root ownership. When `scope_id` is non-null, the composite foreign key
`(scope_id, project_id) -> repository_scopes(id, project_id)` proves exact
Scope geometry.

PostgreSQL `MATCH SIMPLE` gives the intended nullable composite-FK behavior.
Builtin surface uniqueness uses `NULLS NOT DISTINCT`, so a root NULL is not a
loophole. This avoids polymorphic foreign keys and avoids a synthetic target
row whose lifecycle would duplicate Project/Scope.

## Decision 3: One target representation

Persistence adapters are the only layer allowed to map nullable `scope_id` to
the domain union. Public and service-layer contracts use:

```json
{"target":{"kind":"project_root","project_id":"project-id"}}
```

or:

```json
{"target":{"kind":"scope","project_id":"project-id","scope_id":"scope-id"}}
```

`binding_kind`, `is_root`, root Scope objects, and `root_scope_id` are removed.
The target kind is never stored twice.

## Decision 4: Project-first authorization remains mandatory

Human control-plane flow:

```text
JWT -> Organization context -> ProjectGrant -> optional Scope geometry
```

Machine data-plane flow:

```text
credential -> active AccessSurface -> target -> optional active binding and
current human access -> ResolvedRepositoryView -> RuntimeGrant -> admission
```

Project and Scope IDs, URLs, bindings, and credentials never create human
Project access. Credential, route, target, and binding mismatches remain a
non-enumerating Git 401.

## Decision 5: Scope and Access Surface lifecycles are independent

Creating a Scope creates only the path boundary. Enabling Git/CLI is an
explicit, authorized Access Surface operation. A product workflow may create
multiple defaults atomically, but Scope reads and writes cannot assume those
surfaces exist. Raw credentials remain one-time reveal and hash-only at rest.

## Decision 6: Version Engine consumes views, not Scope DTOs

The Project root and non-root Scope target share the same object store and write
engine. Git admission constructs one `ResolvedRepositoryView`; downstream code
does not read `auth["_scope"]` or a public Scope object. Root readiness uses the
Project root hash/ref and root Access Surface, never a synthetic Scope row.

Path-projection state may still use an empty path internally, but it is a view
coordinate rather than resource identity. Root identity fields and externally
visible root-Scope terminology are removed.

## Migration plan

1. Run a read-only preflight and abort on missing/multiple/malformed root rows,
   cross-Project references, binding-kind mismatches, orphan credentials, or
   duplicate surfaces.
2. Pause Scope/Surface/Binding mutations and record a database restore point.
3. In one migration transaction:
   - rename `repo_scopes` to `repository_scopes`;
   - map references to former root rows to `scope_id = NULL`;
   - retain non-root Scope IDs;
   - delete root rows and drop `is_root`/`binding_kind`;
   - install final constraints, indexes, RLS, RPCs, and pre/postflight guards.
4. Deploy the backend/Web contract only after the exact migration SHA passes.
5. Require the new Desktop protocol version rather than serving old DTOs.
6. Verify counts, FK integrity, existing credential continuity, root/scoped Git,
   binding repair, readiness, and audit continuity.

## Rollback

Schema rollback requires the database restore point plus the previous backend;
an application-only rollback is forbidden after destructive columns are
dropped. Before production, staging must demonstrate restore or forward-fix.

## Risks and mitigations

- Existing root bindings could disconnect: preserve binding/credential IDs and
  hashes; migrate only target representation; test real credential continuity.
- NULL could weaken integrity: retain Project FK, composite Scope FK, check
  constraints, `NULLS NOT DISTINCT`, RLS, and real PostgreSQL tests.
- Old clients could misparse responses: explicit protocol version gate; no
  response fallback.
- Root view state could remain a hidden Scope identity: architecture guard and
  semantic inventory classify every root/Scope usage before closure.

## Open questions

None. The user approved the final model and requested direct implementation.
