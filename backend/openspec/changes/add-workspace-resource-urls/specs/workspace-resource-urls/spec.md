## ADDED Requirements
### Requirement: Workspace resource addresses
The system SHALL derive human-facing URLs from website origin, Project ID and repository-relative path without storing per-file URLs.

#### Scenario: File and directory navigation
- **WHEN** an authorized user opens a hosted file or directory URL
- **THEN** the preview SHALL show that path and preserve encoded names across navigation, reload and login.

### Requirement: Admitted snapshot reads
The resource endpoint SHALL check workspace permission and read type, contents or directory entries and revision from one snapshot without a write intent.

#### Scenario: Concurrent change
- **WHEN** the repository changes during a resource read
- **THEN** the response SHALL remain internally consistent and a stale directory-page cursor SHALL be rejected.

#### Scenario: Unauthorized request
- **WHEN** a caller lacks workspace access
- **THEN** no content SHALL be read and a copied URL SHALL not grant membership.

### Requirement: Bounded request cost
Previews SHALL bound response sizes and immediate directory entries and SHALL not enumerate child trees for counts, repository history or S3 keys.

#### Scenario: Wide directory
- **WHEN** a directory contains many child folders
- **THEN** its resource read SHALL use one authorization evaluation and one read snapshot, independent of child count.
