## ADDED Requirements

### Requirement: Shared workbench execution
Multiple admitted Agent Sessions SHALL execute concurrently in one workbench's
current directory and branch without automatic per-Agent copies or write queues.

#### Scenario: Another Agent changes a file
- **WHEN** two Agents are attached to the same workbench
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

#### Scenario: Multiple Chats select one workspace
- **WHEN** two Sessions start concurrently in the same selected workspace
- **THEN** both attach to the same current resource and directory
- **AND** resource allocation is single-flight, not repeated per Chat.

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
other tasks or the user. Trusted inspection SHALL NOT rewrite user Git config.

#### Scenario: Atomic file save and user Git
- **WHEN** an Agent saves through a private temporary file and atomic rename
- **THEN** another Agent and the ordinary-user terminal can read the saved file
- **AND** ordinary Git remains usable without inspection overwriting its configuration.
