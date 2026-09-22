## Context

The existing Project template loader reads mutable files bundled with the API
process and accepts a template ID on Project creation. That is suitable for
first-run examples but not for an open-source application consuming an
independently operated catalog. The official Registry database and object
storage do not exist yet, and this change is explicitly forbidden from
changing the application database.

## Goals / Non-Goals

- Goals:
  - Keep the open-source application independent from any official Registry.
  - Make built-in, official, and custom registries interchangeable providers.
  - Verify an entire immutable release before creating a destination Project.
  - Funnel imported files through the canonical Version Engine write service.
  - Give Web, Desktop, and CLI one stable application API.
- Non-Goals:
  - Persist official listings, releases, or installation receipts.
  - Upload release artifacts or implement the publisher administration plane.
  - Synchronize an instantiated Project with its source release.
  - Copy source Project history, access, integrations, or credentials.

## Decisions

### Decision: Split the hosted Registry from the open-source application

The application owns a provider interface and importer. The hosted Registry
owns listings, immutable release artifacts, signing keys, and publishing. The
application is configured with `TEMPLATE_REGISTRY_MODE` and an optional remote
base URL; no official service credential is shipped in source or binaries.

### Decision: Proxy Registry data through the application backend

Web, Desktop, and CLI call `/api/v1/templates`. Only the backend talks to a
remote Registry. This centralizes URL trust, timeouts, archive limits,
signature verification, entitlement checks, and Version Engine writes.

### Decision: Use a deterministic portable ZIP release

Each release contains `manifest.json` and regular files below `content/`.
The manifest lists every file, its byte size and SHA-256 digest, plus a
canonical aggregate content digest. Import rejects traversal, absolute paths,
backslashes, symlinks, duplicate entries, undeclared entries, secret-like
paths, oversized archives, and digest mismatches.

Remote release metadata contains the SHA-256 of the complete ZIP and may
contain an Ed25519 signature and key ID. Official deployments require a
configured trusted key. Self-hosters can explicitly relax signature policy for
their own Registry; the built-in provider is trusted as installed code.

### Decision: Validate before provisioning, compensate after provisioning

Catalog lookup, bundle download, and full verification complete before a
Project row is created. Provisioning then creates the Project/root scope and
performs one `bulk_write` commit. A failure after Project creation triggers a
best-effort Project deletion so the UI does not retain a partial destination.

### Decision: Keep local starters as a provider, not as an official store

The existing bundled examples remain available through `builtin` mode and for
first-run initialization. They do not imply, authenticate to, or contain the
official hosted Registry. `disabled`, `builtin`, and `remote` are explicit
operator choices.

## Risks / Trade-offs

- No database means no durable idempotency receipt. Clients suppress duplicate
  submits, but cross-process exactly-once behavior is deferred to an approved
  Papertrain application control-plane migration.
- A compensating delete cannot immediately reclaim immutable Git objects; the
  normal object GC process handles unreachable objects.
- Proxying release bytes adds one backend hop, but prevents Desktop/Web from
  becoming trusted archive importers and keeps registry origins configurable.

## Migration Plan

1. Ship canonical APIs and clients with `builtin` as the compatible default.
2. Create the independent Registry Supabase Project, object storage, and
   signing key outside this repository.
3. Configure hosted environments with `remote` mode, URL, and trusted key.
4. Add publishing and durable instantiation records in a separate approved
   database change.
5. Retire legacy `/projects/templates/*` routes only after all clients move.

## Open Questions

- None block this application-layer implementation. Registry persistence and
  publishing are intentionally a later database/storage change.
