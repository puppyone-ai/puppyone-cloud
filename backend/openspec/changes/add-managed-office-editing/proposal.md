# Change: Add PuppyOne-managed Office editing

## Why

Desktop users must be able to edit DOCX, XLSX, and PPTX files inside PuppyOne
without installing Docker, configuring an editor server, or exposing a local
HTTP bridge. The existing desktop prototype delegates engine configuration and
file callbacks to each client, which is not an acceptable product boundary.

## What Changes

- Add an authenticated, short-lived Office session API under `/api/v1/office`.
- Keep source and edited binaries in private PuppyOne object storage and keep
  expiring session state in shared Redis.
- Make PuppyOne backend the only holder of ONLYOFFICE URLs and JWT secrets.
- Give the document engine narrowly scoped, expiring source and callback
  capabilities that reveal neither desktop paths nor Cloud Project authority.
- Let Desktop download completed revisions and apply them through its existing
  version-checked atomic binary writer.
- Remove Document Server, JWT, callback bridge, and Docker-oriented settings
  from the Desktop product contract.

## Impact

- Affected specs: managed-office-editing, s3-storage
- Affected code: `src/platform/office`, `src/config.py`, `src/main.py`, Desktop
  Electron Office adapter, Desktop environment contract, and Office architecture
  documentation

