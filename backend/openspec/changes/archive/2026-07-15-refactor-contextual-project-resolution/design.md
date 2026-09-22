# Design: Contextual Desktop Project resolution

## Context

Canonical root and scoped Git URLs are stable, non-secret locators. They do not
grant access. WorkspaceBinding is durable local attachment identity, while the
current JWT ProjectGrant authorizes human Project UI. The current Desktop path
incorrectly requires a binding before canonical navigation and falls back to a
broad catalog when no binding exists.

## Goals / Non-Goals

- Resolve one open Local workspace to an exact authorized Project/Scope,
  local-only, or recovery.
- Never enumerate Organization Projects to infer contextual identity.
- Keep canonical locator, ProjectGrant, WorkspaceBinding, and RuntimeGrant
  independent.
- Keep global Cloud Project browsing available outside Local context.
- Do not create bindings, credentials, content, S3 objects, or Version Engine
  transactions during context resolution.

## Decisions

### Canonical resolver returns authorized context

The existing canonical endpoint returns Project metadata/current capabilities,
the exact `ProjectRootTarget | ScopeTarget`, and the optional Scope path. It
does not duplicate root identity through `scope_id`/`binding_kind`, and does
not return `requires_confirmation` or any credential. The legacy endpoint
retains its separate candidate response.

### Desktop owns one discriminated context state

Desktop reduces binding/config/all Git remote facts into resolving, resolved,
local-only, or recovery. Contextual project-data loading is idle without an
authorized Project ID and cannot import/call the catalog loader.

### Binding is a fast path, not a navigation prerequisite

A verified binding resolves directly. A canonical locator without a binding
may also resolve directly after server authorization, with `not-bound` status.
Only explicit connect/repair/detach changes binding or credential state.

### Ambiguity fails closed

All fetch/push remotes are normalized. Any differing origin, Project, Scope or
kind produces recovery; iteration order and remote name cannot select identity.

### Dependency outage is distinct from missing authority

Authorization or workspace-binding storage failures fail closed as generic
retryable 503 responses. They are not converted to a missing Project, binding,
Scope, or grant because an unavailable dependency proves none of those facts.
Safe control-plane reads retry one HTTP transport failure; writes are not
automatically replayed. Electron transports the HTTP status across IPC, and
Desktop retains an already verified exact context for retryable failures while
showing a temporary recovery state when no context has been verified.

## Risks / Trade-offs

- Global browsing can regress if catalog code is simply removed. Mitigation:
  retain a separate explicit global hook and integration test.
- Locator parsing could be mistaken for authorization. Mitigation: render no
  Project metadata until the backend returns authorized context.
- Stale responses can select a previous Project. Mitigation: key/cancel by
  workspace instance, origin, session generation and locator/binding identity.
- A dependency outage can look like deletion and discard valid context.
  Mitigation: preserve 503 semantics end-to-end and never cache an unavailable
  authorization or binding-storage read as a missing domain fact.

## Migration Plan

1. Add the backend authorized canonical response and tests.
2. Add Desktop canonical client, locator aggregation and context controller.
3. Split contextual data from global catalog.
4. Remove Local workspace catalog UX and add recovery states.
5. Run backend/Desktop regression suites; no database rollout is required.
