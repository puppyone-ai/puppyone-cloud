# Change: Harden machine credential storage

## Why

Scope, agent, and sandbox bearer credentials currently have plaintext storage or
plaintext lookup paths. A read-only database leak can therefore become direct
tenant data-plane access, and revocation behavior differs by surface kind.

## What Changes

- Require the dedicated `ACCESS_CREDENTIAL_HASH_SECRET` for credential hashing.
- Make scope writes and authentication hash-first unconditionally; restrict
  plaintext fallback to legacy rows whose hash has not been backfilled.
- Store newly issued agent and sandbox credentials only in
  `access_surface_credentials`, never in `access_surfaces.config`.
- Preserve a bounded legacy read path and provide an idempotent, manifest-driven
  data migration that removes migrated config secrets.
- Make create/regenerate the only plaintext issuance responses and verify that
  revocation rejects the old credential on the next authenticated operation.

## Impact

- Affected specs: `machine-credential-security` (new)
- Affected code: credential, scope, agent, sandbox repositories and migrations
- Deployment: the portable data migration must verify before the legacy
  fallback Contract is removed
