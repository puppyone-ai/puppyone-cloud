## ADDED Requirements

### Requirement: Explicit snapshot object recovery
A breaking release SHALL accept unversioned object storage only when the release configuration declares `object_recovery.mode = "snapshot"` with a non-empty approver and reason.

#### Scenario: Unversioned storage without approval
- **WHEN** the object or backup bucket is not versioned and no snapshot policy is declared
- **THEN** recovery preflight fails before any service stops

### Requirement: Verified snapshot after quiescence
In snapshot mode the release SHALL copy every current object into the separate private backup bucket after writers stop, verify each copy by SHA-256, re-list the source, and store a digest-bound manifest in release evidence.

#### Scenario: A writer is still active
- **WHEN** the object inventory changes while the snapshot is taken
- **THEN** the release fails without applying schema or data changes

### Requirement: Safe restore
Restoring a snapshot SHALL verify the manifest digest against release evidence and each backup object against the manifest before writing, and SHALL NOT delete objects created after the snapshot.

#### Scenario: Damaged backup copy
- **WHEN** a backup object differs from its manifest entry
- **THEN** restore stops and the target object is unchanged
