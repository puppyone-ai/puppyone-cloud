# Change: Add a portable template registry client

Status: **approved by the user on 2026-07-14 for implementation without database changes**

## Why

Papertrain is open source and self-hostable, so the application cannot couple
project creation to an official PuppyOne database or storage account. It needs
a provider-neutral registry contract that can be disabled, backed by bundled
starters, or pointed at an independently hosted official/custom registry.

## What Changes

- Add a versioned, provider-neutral Template Registry client boundary.
- Define a portable ZIP bundle format with a canonical manifest, per-file
  digests, bounded resource limits, path safety, and optional Ed25519 trust.
- Add canonical catalog, detail, status, and instantiate APIs under
  `/api/v1/templates` while retaining the legacy Project template routes.
- Create a fresh Project and one initial Version Engine commit from a verified
  release; never copy source authorization, history, credentials, or bindings.
- Add Web, Desktop, and CLI consumers that use the canonical API contract.
- Keep official Catalog persistence, release object storage, publishing, and a
  durable instantiation ledger out of this change until the separate Registry
  Supabase/S3 deployment exists.

## Impact

- Affected specs: `template-registry` (new)
- Affected backend: `src/platform/template_registry`, Project creation wiring,
  configuration, API registration, and tests
- Affected clients: Web template gallery, Desktop Cloud template gallery, CLI
- Affected documentation: `docs/architecture/14-template-registry.md`
- Database impact: none; no schema or data migration is included
