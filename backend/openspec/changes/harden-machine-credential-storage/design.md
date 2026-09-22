## Context

`access_surface_credentials` already provides an HMAC-hashed credential store
for MCP endpoints. Scope credentials have a transitional hash column, while
agent and sandbox credentials remain in provider config JSON.

## Goals / Non-Goals

- Goals: no new agent/sandbox plaintext credentials; mandatory scope hashes;
  hash authentication; immediate revocation; safe staged migration.
- Non-Goals: OAuth token encryption, physical deletion of the legacy scope
  column, KMS integration, or a reveal endpoint.

## Decisions

- Reuse `access_surface_credentials` as the single bearer-token store.
- Treat create/regenerate responses as one-time issuance. Ordinary reads expose
  only prefix/last-four metadata.
- Keep a legacy config lookup only when no active hashed credential exists for
  the resolved surface. The manifest-driven data migration removes config
  plaintext only after inserting or confirming the hash row.
- Scope authentication always queries `access_key_hash` first. Plaintext lookup
  additionally requires `access_key_hash IS NULL`, so newly written rows can
  never authenticate through the legacy path.
- Credential hashing has no JWT/internal-secret fallback. Test environments
  inject an explicit credential secret.

## Risks / Trade-offs

- Backfill interruption can leave mixed rows. It is idempotent and removes the
  plaintext only after the hashed row exists.
- Existing clients that expected list/get to replay a secret must use
  regenerate once; silently retaining replay would preserve the vulnerability.

## Migration Plan

1. Deploy schema constraint and repository dual-read support.
2. Run and verify the idempotent `20260711_surface_credentials` artifact
   through the portable data migration runner.
3. Observe legacy-fallback usage until zero.
4. Remove legacy agent/sandbox fallback in a later cleanup.
5. Drop `repo_scopes.access_key` only in a separately approved migration.

## Open Questions

- None for this stage; OAuth encryption and final legacy-column removal are
  deliberately separate changes.
