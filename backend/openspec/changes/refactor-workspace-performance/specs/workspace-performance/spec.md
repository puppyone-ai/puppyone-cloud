## ADDED Requirements

### Requirement: Stable project workspace state
The application SHALL separate URL location, remote query state, project view
state and editor drafts with explicit ownership and project isolation.

#### Scenario: Return to a previously opened view
- **WHEN** a user changes Files to Git and back without changing projects
- **THEN** the workspace navigation remains responsive
- **AND** the last selected file and editor session are restored without discarding dirty edits

#### Scenario: Open project utilities
- **WHEN** a user opens Access or Chat from the right side of the project header
- **THEN** the current Files or Git view remains mounted as the main content
- **AND** both utilities use one mutually exclusive resizable right sidebar
- **AND** an unsaved Access configuration may reject closing or switching the sidebar

#### Scenario: Primary project views share stable chrome
- **WHEN** a user switches between Files and Git
- **THEN** the project layout keeps one 46px header mounted in the same position
- **AND** only the breadcrumbs, page-specific actions, and body content change
- **AND** the header cannot flex-grow into the body viewport

#### Scenario: Change project
- **WHEN** the project identity changes
- **THEN** selection and query data from the old project are not presented as the new project

### Requirement: Non-blocking authorized reads
Audited async read paths SHALL execute synchronous database/storage work outside
the event loop under a bounded concurrency policy, without bypassing authorization.

#### Scenario: Slow activity database read
- **WHEN** the activity repository is blocked waiting for I/O
- **THEN** another lightweight request in the same worker remains schedulable
- **AND** activity data is returned only after the canonical project grant allows it

### Requirement: Bounded version reading and rendering
Version synchronization and diff presentation SHALL bound database, object-store,
CPU and DOM work while preserving canonical head and replay semantics.

#### Scenario: First WebSocket reconciliation
- **WHEN** the client needs only the current canonical head
- **THEN** it does not fetch or hydrate a large history page
- **AND** subsequent live/replayed events remain ordered and deduplicated

#### Scenario: Oversized textual diff
- **WHEN** a file exceeds the supported preview work or output budget
- **THEN** the UI shows an explicit bounded preview/fallback instead of allocating an unbounded line list

### Requirement: Convergent frontend effects
Disabled, pending, failed and empty queries SHALL NOT cause self-sustaining
render/effect updates in the project workspace.

#### Scenario: Non-structured file
- **WHEN** a Markdown file does not enable the tools query
- **THEN** access points settle to an empty state without repeated state writes

### Requirement: Regression evidence
Each refactored boundary SHALL have executable behavioral coverage and documented
verification limits; type checks alone SHALL NOT count as performance validation.

#### Scenario: Performance handoff
- **WHEN** implementation results are reported
- **THEN** deterministic unit/concurrency results are distinguished from browser and production measurements

### Requirement: Truthful staged workspace loading
Workspace navigation and directory views SHALL distinguish unknown, loading,
successful empty and failed reads, with independently loadable content lanes.

#### Scenario: Cold navigation
- **WHEN** authentication, organization discovery or the first project-list read is pending
- **THEN** the rail shows a loading placeholder, not an empty list or synthetic current-project-only list
- **AND** the active project's content need not wait for the whole navigation list

#### Scenario: Different resource identity
- **WHEN** the organization, project or directory key changes
- **THEN** the previous identity's payload is not displayed under the new identity
- **AND** same-path folders in different projects have independent expansion state

#### Scenario: Failed read and recovery
- **WHEN** a project list, folder or file cannot be read
- **THEN** the affected surface offers an error and retry, not empty content
- **AND** failed or pending text content cannot be submitted as an editor save
- **AND** same-identity background refreshes preserve the last successful snapshot

#### Scenario: Foreground directory priority
- **WHEN** the active project's root has not yet returned a snapshot
- **THEN** the Files decoration lane does not request tools, sync status, endpoints, scopes, connectors or repo identity
- **AND** the root request is deduplicated across layout and explorer consumers
- **AND** navigating projects does not enumerate their file contents in advance
