# Change: Move the hosted repository API into Access

## Why

The approved second refactor batch gives Web/Desktop repository HTTP handlers
an explicit Access adapter owner. Version Engine keeps Git read/publication
implementation; the existing file command extraction remains in place.

## What Changes

- Move the existing content, resource, history, repository management, audit,
  download and WebSocket modules into `src/platform/access/adapters/api`.
- Put the existing shared wire models in `src/platform/access/repository_schemas`
  so Git and CLI consumers do not import the Web adapter.
- Update application mounting, callers and tests; remove the old HTTP module
  paths instead of maintaining a second set of route exports.
- Keep AP-FS and its credential resolver in place for a later batch.

## Impact

- Affected spec: `project-structure`.
- Preserve public URLs, authorization, schemas, signed downloads, notifications
  and existing read/write I/O. No database, frontend, runtime/provider or Git
  publication behavior changes.
- Product adapter/tree algorithm ownership and command compatibility removal
  remain separate follow-up work.
