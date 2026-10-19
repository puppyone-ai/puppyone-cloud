# Change: Complete file operations and CLI Access boundaries

## Why

The user approved batches three and four: finish Product adapter/kernel
ownership, then split the CLI AP-FS entrypoint. Credential management belongs
to Access while CLI/Git and MCP retain separate credential types and admission.

## What Changes

- Move ProductOperationAdapter into file_operations and tree_patch into the
  Git write engine; extract the existing read context into NativeTreeReader's
  module so history never depends on file_operations.
- Update callers and remove retired Product command compatibility exports.
- Split AP-FS into CLI router, context, read, write and search modules.
- Move the shared credential repository and existing Git/CLI resolver to
  Access. Keep MCP resolution, issuance, expiry, revocation and type filtering
  behavior unchanged; no credential conversion or new authentication scheme.
- Move shared file upload rules to file_operations; preserve values and callers.

## Impact

- Affected spec: project-structure. Existing URLs, schemas, error behavior,
  request identity, snapshots and Git publication remain unchanged.
- No database, frontend, deployment or sandbox-provider changes. Git protocol
  directory relocation and enabling native Scope/MCP remain separate work.
