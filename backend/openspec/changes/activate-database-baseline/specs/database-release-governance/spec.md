## ADDED Requirements

### Requirement: Archived baseline source history
The repository SHALL keep the executable baseline and later migrations in
`supabase/migrations`, and preserve covered sources unchanged in a separately
checksum-verified archive that normal deployment does not execute.

#### Scenario: Fresh installation
- **WHEN** an empty Supabase database applies the active migration directory
- **THEN** B1 and later migrations execute without replaying archived SQL

### Requirement: Verified existing database adoption
Baseline adoption SHALL verify complete source history and schema before
atomically preserving original history and replacing covered tracking rows.
It MUST NOT rerun baseline DDL or manufacture data-job completion receipts.

#### Scenario: Existing populated database
- **WHEN** history and schema match the approved baseline
- **THEN** history adoption preserves all business rows and subsequent db push is clean

#### Scenario: Drift or incomplete upgrade
- **WHEN** a source version is missing or schema permissions differ
- **THEN** adoption fails without changing migration history or customer data
