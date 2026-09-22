## ADDED Requirements

### Requirement: Root Git readiness gates Claude

The system MUST derive Claude readiness from an active root Git surface, an
accepted canonical root head, and a committed root `access_git` transaction.
It MUST NOT infer readiness from Project metadata, Product/API writes, or a
non-root scope.

#### Scenario: Root Git is absent

- **WHEN** a Project has no active Git surface bound to its canonical root scope
- **THEN** readiness reports `git_not_created`
- **AND** Desktop shows Create Git without loading Claude runtime

#### Scenario: Initial push is absent

- **WHEN** the root Git surface exists but the canonical root scope has no
  accepted head or no committed root Git-push transaction
- **THEN** readiness reports `awaiting_first_push`
- **AND** Desktop shows the remote and first-push instructions

#### Scenario: Root push is accepted

- **WHEN** an accepted root push publishes the canonical root head
- **THEN** readiness reports ready
- **AND** Desktop may load the Project Agent and Claude runtime

#### Scenario: Product write is not a Git push

- **WHEN** a Web/API/seed operation creates a canonical root head before any
  accepted root Git push
- **THEN** readiness remains `awaiting_first_push`
- **AND** Desktop does not load Claude runtime
