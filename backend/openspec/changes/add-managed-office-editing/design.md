# Design

## Context

The Desktop owns local filesystem authority and the backend owns the managed
ONLYOFFICE integration. Local folders are not Cloud Projects and a local path is
never an authorization fact. ONLYOFFICE Docs requires a document editing
service, so the service is deployed by PuppyOne and is invisible to end users.

## Goals / Non-Goals

- Goals: no user-side Docker or engine configuration; embedded PuppyOne UX;
  authenticated multi-replica sessions; bounded binary transport; safe callback
  verification; local atomic persistence and conflict recovery.
- Non-goals: registering local devices or paths in Cloud; collaborative Project
  editing; retaining Office files after session expiry; accepting arbitrary
  callback/download origins.

## Decisions

- Desktop uploads bytes plus a filename to an authenticated user-scoped session;
  it never uploads the absolute local path.
- Private S3 objects hold source and result bytes. Redis holds owner, object keys,
  document key, state, revision, and expiry with a hard TTL.
- Public source/callback routes use purpose-bound HMAC capabilities. Callback
  bodies additionally require the configured ONLYOFFICE JWT and exact document
  key match.
- Result downloads and redirects are accepted only from configured HTTPS
  origins, are streamed with a size cap, and are written back to private S3.
- Desktop polls authenticated session state. A new result revision is downloaded
  and committed with the original local version as the compare-and-swap base.
- A local conflict keeps edited bytes in Desktop recovery storage. Cloud never
  decides which local version wins.
- Shared UI continues to consume the engine-neutral `OfficeEditingPort`; Cloud
  auth, transport, native surfaces, and filesystem writes remain Electron Host
  responsibilities.

## Risks / Trade-offs

- A managed editor requires network connectivity. The existing read-only local
  preview remains the fail-closed fallback.
- Temporary Office binaries are sensitive. They use private storage, opaque
  object keys, short TTL state, explicit cleanup, and no absolute path metadata.
- A callback may race a client poll or close. Monotonic result revisions and
  idempotent cleanup prevent stale results from replacing newer ones.

## Migration Plan

1. Deploy the disabled backend API and configure private storage/Redis.
2. Configure the PuppyOne-managed Document Server and enable backend health.
3. Ship the Desktop Experimental client that uses only `/api/v1/office`.
4. Enable the experiment for internal users; read-only preview remains fallback.
5. Remove the legacy local bridge and Desktop engine environment variables.

## Open Questions

- None. The user explicitly approved the PuppyOne-managed service boundary.

