## Status
Approved for implementation by the user on 2026-11-07. SQL, backend and Desktop reuse the existing organization/project publication model. Acceptance evidence and deployment are recorded separately.

## Context
Desktop already publishes local Git repositories into an explicit organization. Homepage items already carry organization IDs, but organization responses do not carry a reliable personal-space identity. Historical `type`, slug, creator and member count cannot safely establish personal ownership.

## Goals / Non-Goals
- Distinct personal and named organization groups, including an empty writable destination.
- A local project drop starts the existing confirmation and Git publication flow with an explicit target; retain the local project.
- Reuse projects, Git storage and publication admission, not a second Cloud system.
- No automatic migration of existing content, source deletion, continuous synchronization, remote rebinding, cross-organization move, or compute allocation.

## Decisions
1. Add an immutable unique personal-owner binding to organization-backed personal spaces. New personal spaces have exactly their owner as an organization member. Team management cannot invite into, transfer or delete them.
2. Preserve all existing organizations, projects, memberships and default selections. Provision a separate empty personal space for an existing account. Existing unbound organizations remain team destinations even when their historical label says personal.
3. Use a service-only atomic and idempotent provisioning operation; never trust a client-supplied owner ID. Compose account initialization and entitlements in the appropriate upper-layer orchestration module, respecting import boundaries.
4. Discovery returns a reliable kind and caller capability. Server-side creation still checks current membership and quota. Current SQL creation admission accepts organization members without inspecting their organization role; do not silently replace that rule with a client-side viewer-role guess. Project editing remains separately authorized.
5. Bind the Desktop publication intent to the exact local workspace, current account/deployment, and selected destination. Navigation failure, cancellation or account changes must not publish another workspace. Existing publication recovery retains its original target.
6. Do not overwrite an existing PuppyOne remote or link. Already-linked projects need an explicit separate-copy workflow, not implicit rebinding through drag/drop.

## UI and publication scope
- Dropping is not an immediate upload: show destination, Git publication scope and visibility before confirmation.
- Provide a keyboard/menu alternative and clear loading, unavailable and non-writable destination states.
- Existing Git publication rules govern committed changes and ignored files. Do not label the operation as copying every local file. Clearly disclose unpublished changes and large-file limits.
- Failed uploads retain recoverable publication state without silently retrying into a different organization.

## Validation / rollout
- Focused native PostgreSQL populated upgrade: preserve old teams/projects/defaults, concurrent ensure, immutable ownership and personal membership guards.
- Focused API tests: human identity, capability/discovery, creation admission, lifecycle and first-sign-in behavior.
- Desktop component/intent tests: empty/multiple groups, drop target, keyboard alternative, cancellation, account/workspace changes, failed publication/retry and existing links; visual acceptance in relevant layouts/themes.
- Refresh qubits and inspect the full release diff and applied-version set before database-affecting integration. A local PostgreSQL proof does not establish hosted Supabase or application acceptance.
- Update canonical documentation with committed-source provenance. Keep tested, merged, database-ready, deployed and accepted evidence distinct. Run the full source checks only after implementation is complete, as requested on 2026-11-07. Do not modify application main.

## Legacy-account disposition
Preserve every old space and its content. Ensure a distinct personal destination on demand. The read-only Qubits inventory has 55 existing spaces (4 legacy personal labels, 51 team labels); none is reclassified. A future explicit classification migration would need trustworthy reviewed inputs.

## Release preflight
On 2026-11-07 the staging-only Supabase management read query confirmed 81 applied migration versions, latest `20261107110000`; the only pending candidate is `20261107120000`, with no unknown or older missing migrations. Full diff is from `puppyone-cloud@b3a66034`; no historical migration bytes are changed. The candidate populated-upgrade test runs an owned PostgreSQL 17 cluster, not hosted infrastructure.
