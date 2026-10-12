# Change: Add workspace resource URLs

## Why
Hosted files and folders need human-facing URLs using the existing Project identity and repository path.

## What Changes
- Add a read-only website route and thin content resource endpoint, reusing admitted snapshots and native readers.
- Generate encoded links in Web and Desktop, preserve login return paths and provide a preview with a Desktop download link.
- Bound previews and directory pages; verify authorization, snapshot consistency and request counts.

## Impact
- Affected specs: workspace-resource-urls
- Affected code: Version Engine HTTP/read boundaries, Web preview and navigation, Desktop link generation.
- No schema migration, new reader layer, write intent or external guest authorization changes.

The user approved this architecture and implementation in the current conversation. Canonical design: puppy-issues/document/puppyone/architecture/03-project-repository-model.md.
