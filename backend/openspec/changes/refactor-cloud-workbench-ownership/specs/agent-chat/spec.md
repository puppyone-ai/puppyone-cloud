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

### Requirement: Existing architecture compatibility
Workbench changes SHALL preserve existing Session clients, native Git publication
and human authorization boundaries and SHALL NOT modify Desktop execution.

#### Scenario: Existing client omits a workbench
- **WHEN** the existing Session request is submitted
- **THEN** its established admission, recovery and publication behavior is retained
- **AND** regression evidence is recorded before promotion.
