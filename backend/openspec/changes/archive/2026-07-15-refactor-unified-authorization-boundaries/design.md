## Context

PuppyOne has three independent questions: whether a human belongs to an
Organization, what that human may do to a Project, and what a machine principal
may do within a scope. Existing code mixes those questions. Desktop also treats
Git remotes and access keys as Project identity. The result is inconsistent
authorization and an ambiguous Cloud workspace state.

The user explicitly approved this proposal through ISSUE-029 and requested the
final architecture without preserving lower-quality compatibility paths.

## Goals / Non-Goals

- Goals: one human Project PDP; fixed role/capability contract; explicit local
  binding; separate runtime credentials; root-Git readiness; fail-closed tests
  and database integrity.
- Non-goals: custom roles, outside collaborators, human folder ACLs, a separate
  Zanzibar/OpenFGA deployment, or a persistent Local/Hybrid/Cloud-only Project
  type.

## Decisions

### Human authorization

`AuthorizationService` is the only component that resolves a `ProjectGrant`.
Resolution order is:

1. missing Organization membership -> deny;
2. Organization owner -> inherited Project Admin;
3. explicit `project_members` role -> that role;
4. `projects.visibility = 'org'` -> inherited Viewer;
5. otherwise -> deny.

Unknown roles, mismatched tenant facts, and repository failures deny access.
Routes authorize named actions; they never branch on raw role strings.

### Fixed capabilities

- Viewer: read Project/content/history/agent/access metadata, bind read-only.
- Editor: Viewer plus content mutation, history restore, Agent/Automation
  execution, and read-write binding.
- Admin: Editor plus Agent/Automation/Integration/Scope/Access management,
  Project settings, members, sharing, access credentials, deletion, and
  binding administration.

### Data model

The final authorization/binding/runtime subsystem has exactly nine core tables:
`organizations`, `org_members`, `projects`, `project_members`,
`project_workspace_bindings`, `repo_scopes`, `access_surfaces`,
`access_surface_credentials`, and `access_surface_policies`.
`repo_user_permissions` is removed only after the deterministic, manifest-driven
data migration has verified receipts in Qubits and Production. The reviewed
contract remains outside Supabase schema history until that promotion gate.

Tenant integrity is enforced with composite foreign keys. Project creation and
creator Admin membership are one database transaction. Active bindings are
unique per stable workspace instance. A full binding must reference the
canonical root scope; a scoped binding must reference a non-root scope.

### Binding is identity, not authorization

A binding stores stable identifiers only: Cloud origin, Project, scope,
workspace instance, bound user, kind, mode, and lifecycle status. It stores no
absolute path, bearer token, role snapshot, or capability snapshot. Every
binding request re-resolves the current ProjectGrant. Revoking membership,
downgrading a role, switching account/host, or revoking the binding invalidates
Cloud access on the next request while leaving local files available.

### Runtime credentials

Each binding receives its own hash-only credential. Its mode is bounded by both
the selected scope and the current human capability. Runtime credentials resolve
only to RuntimeGrant and cannot access the human control plane.

### Git and Claude readiness

Readiness is derived, never cached as a mutable boolean:

`claude_ready = active root git surface exists AND canonical root scope has an
accepted head AND the Version Engine ledger contains a committed root
access_git transaction`.

An empty Project, a Product/API-created head, a non-root surface, or a non-root
head does not qualify. The Desktop renders `Create Git` or `Push your first
commit` and does not request Agent/Claude runtime data until ready.

## Risks / Trade-offs

- A broad cutover can expose hidden legacy permission rows. Mitigation: a
  blocking preflight reports `denied`, scoped, orphan, and tenant-mismatched
  rows before schema retirement.
- Central policy can become a hot path. Mitigation: request-scoped immutable
  grant caching keyed by user and Project, with revocation checked on each new
  request and no cross-request role cache.
- Desktop can retain stale asynchronous responses after account changes.
  Mitigation: key binding state by Cloud origin, account subject, workspace
  instance, and request generation; discard mismatched completions.

## Migration Plan

1. Add hardened `project_members`, bindings, binding credential relation,
   transactional RPCs, and readiness query.
2. Run the blocking, manifest-driven legacy data migration to copy
   admin/editor/reader to admin/editor/viewer, and reject
   denied/scoped/tenant-invalid ambiguity.
3. Switch every Project-scoped human entry point to the canonical PDP.
4. Switch Desktop to explicit bindings and readiness.
5. Remove legacy routes, repositories, heuristic resolver, and any v1 policy
   switch. Promote the schema contract only after both environment receipts;
   the final runtime has only the v2 policy.

Rollback before legacy drop is additive. After drop, recovery uses the verified
preflight snapshot and forward repair; no `old_allow OR new_allow` mode exists.
