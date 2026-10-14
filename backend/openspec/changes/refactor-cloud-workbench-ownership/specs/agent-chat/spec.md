## ADDED Requirements

### Requirement: Shared workbench execution
A workbench SHALL belong to one user only. That user's admitted Agent Sessions
SHALL execute concurrently in its current directory and branch without automatic
per-Agent copies or write queues. Project access SHALL NOT grant access to another
user's live workbench.

#### Scenario: Project colleague cannot enter the private workspace
- **WHEN** a different user with access to the same Project requests a workspace connection
- **THEN** ownership validation rejects the request before provider access
- **AND** neither the directory nor terminal/preview/task capabilities are shared.

#### Scenario: Another Agent changes a file
- **WHEN** two Agents of the same user are attached to that user's workbench
- **THEN** each sees the other's saved changes in the same directory
- **AND** conflicts are handled as normal user-managed filesystem/Git conflicts.

### Requirement: Independent task lifetime
Stopping or recovering a Run SHALL NOT terminate unrelated workbench processes
or restore the shared directory from that Run's conversation checkpoint.

#### Scenario: Agent exits while a terminal and another Agent run
- **WHEN** one Agent completes, fails or is stopped
- **THEN** the other Agent and terminal remain alive with their current files
- **AND** only the durable workbench lifecycle owner can pause or retire compute.

### Requirement: Selected workspace is the execution owner
New Sessions and Runs SHALL bind the selected workbench. The system SHALL NOT
fall back to Session-owned compute. Pre-launch Chats need not be migrated.
Native Git authority, authorization and local Desktop execution remain unchanged.

#### Scenario: Request omits its selected workbench
- **WHEN** a new Run is submitted without a workbench
- **THEN** admission rejects it before allocating resources
- **AND** no legacy Session sandbox is created.

#### Scenario: Platform creates the workspace
- **WHEN** the user creates a workspace
- **THEN** the platform persists the owner and allocation intent, provisions a resource and initializes its directory once
- **AND** repeated creation reconciles the same operation before acknowledging readiness.

#### Scenario: Multiple Chats select one workspace
- **WHEN** two Sessions start concurrently in the same selected ready workspace
- **THEN** both attach to the already-created current resource and directory
- **AND** neither Chat allocates a resource, clones the repository or wakes compute.

#### Scenario: Selected workspace is not running
- **WHEN** a Chat attempts to attach to a paused, missing or unready workspace
- **THEN** task admission rejects the attachment without provider creation or resume
- **AND** creation, wake and reconstruction remain workspace lifecycle operations.

### Requirement: Independent native publication target
Workbench publication SHALL advance only its explicit native target ref using
captured generation, expected OID, candidate and original operation receipt.
Repository HEAD SHALL remain the existing display source and SHALL NOT change
implicitly when a non-primary workbench publishes.

#### Scenario: Display source changes independently
- **WHEN** the repository HEAD binding changes after a non-primary candidate is captured
- **THEN** that candidate remains eligible for its own target ref CAS
- **AND** publication neither changes HEAD nor merges into the display branch.

#### Scenario: Another task advances the local worktree
- **WHEN** a Run retries its captured candidate after another task makes a local commit
- **THEN** it publishes only the original candidate if native ref CAS still permits it
- **AND** it does not finalize again or substitute the new HEAD.

### Requirement: Shared files and ordinary Git remain usable
Task process isolation SHALL NOT make shared workspace files inaccessible to
other Agents of the SAME user or that user's terminal. They SHALL share one
ordinary-user filesystem identity, independent of task process-stop isolation.
Trusted inspection SHALL NOT rewrite user Git config.

#### Scenario: Atomic file save and user Git
- **WHEN** an Agent saves through a private temporary file and atomic rename
- **THEN** another Agent and the ordinary-user terminal can read the saved file
- **AND** ordinary Git remains usable without inspection overwriting its configuration.
