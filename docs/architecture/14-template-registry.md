# Template Registry Architecture

## Boundary

Papertrain is open source and self-hostable. The application therefore owns a
portable Registry protocol and a safe importer, while an official or custom
Registry is a separately deployed service with its own database, release
storage, signing keys, and publishing controls.

```text
Papertrain Web / Desktop / CLI
              |
              | /api/v1/templates
              v
Papertrain application backend
  - provider selection
  - archive limits and trust policy
  - Organization entitlement check
  - Project + Version Engine write
              |
              | versioned read-only Registry HTTP protocol
              v
Official or custom Template Registry
  - catalog metadata database
  - immutable release object storage
  - publisher administration and signing
```

No client receives Registry service-role credentials or direct database
access. The backend does not assume that a Registry shares its Supabase Project,
S3 account, user identities, or deployment lifecycle.

## Provider modes

`TEMPLATE_REGISTRY_MODE` has three explicit values:

- `disabled`: catalog discovery and instantiation are unavailable;
- `builtin`: trusted starter content installed with the open-source release;
- `remote`: a configured, independently operated Registry is used.

Built-in templates are local application assets, not a bundled copy of the
official store. Hosted Papertrain will switch to `remote` after its Registry
database, object storage, and signing key are provisioned. A self-hoster may
disable the feature or point to a compatible Registry.

## External Registry v1 read contract

The configured base URL exposes:

```text
GET /v1/templates
GET /v1/templates/{template_id}
GET /v1/templates/{template_id}/releases/{release_id}/bundle
```

List and detail return portable JSON metadata. Bundle returns a ZIP body from
the same configured origin; metadata never supplies an arbitrary download URL.
The application does not follow redirects. This avoids turning the backend
into a server-side request forgery proxy. Metadata and bundle bodies are read
with separate streaming byte ceilings before schema validation.

Each release describes:

```text
id, version, bundle_sha256, file_count, total_bytes,
published_at, signing_key_id?, signature?
```

For a trusted remote Registry, `signature` is Ed25519 over the domain-separated
bundle SHA-256 digest. Trusted public keys are deployment configuration, not
catalog data.

## Portable bundle v1

```text
template-release.zip
├── manifest.json
└── content/
    └── arbitrary regular files
```

The manifest declares the template/release identity, every file path, byte
size and SHA-256, and one aggregate content digest. The importer reads bytes
without extracting them to disk and rejects:

- absolute paths, `..`, backslashes, control characters, excessive depth or
  length;
- non-NFC or non-portable names, case-insensitive collisions, and paths that
  would be both a file and a directory;
- duplicates, undeclared files, non-regular entries, symlinks and encryption;
- `.git`, `.ssh`, live `.env`, private-key, or credential-like paths and
  embedded private-key markers;
- excessive compressed bytes, files, individual size, or expanded total size;
- identity, inventory, content, archive digest, or required signature mismatch.

These checks happen before the destination Project exists.

## Instantiation

```text
resolve destination Organization and capacity
  -> resolve release
  -> download and verify complete bundle
  -> create Project + Admin grant + canonical Project repository state
  -> VersionWriteCommandService.bulk_write(...)
  -> return the new independent Project
```

Only content files cross the boundary. Project history, organizations,
memberships, access surfaces, credentials, connectors, OAuth grants, workspace
bindings, logs, webhooks, and jobs are never part of the bundle.

The release creates one fresh template commit. Existing instances do not
receive later release changes. A failure after provisioning triggers a
best-effort Project delete; unreachable version objects are reclaimed by normal
GC.

## Deferred control-plane work

The following require the separately approved Registry database/storage
change and are deliberately absent here:

- listing and release persistence;
- source Project publishing and immutable artifact upload;
- moderation, visibility, deprecation, and rollback pointers;
- download analytics;
- Registry production/staging Supabase Projects and storage buckets.

Durable instantiate idempotency keys and installation audit receipts belong to
the Papertrain application control plane because they bind an authenticated
user and Organization to a destination Project. They also require an approved
application database migration and are therefore deferred separately; clients
still suppress duplicate submits in this database-free change.

Their absence does not change the client or import contract defined here.
