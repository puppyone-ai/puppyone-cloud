## ADDED Requirements

### Requirement: A Space retains its identity while membership grows
The system SHALL use the same Space and project IDs when an owner invites
additional members. Default-space status SHALL NOT prohibit collaboration.

#### Scenario: A single member invites a colleague
- **GIVEN** a signed-in owner of a Space with one member
- **WHEN** the owner invites another member and the existing seat admission succeeds
- **THEN** the colleague joins the same Space through the existing invitation flow
- **AND** the system creates no replacement Space or project.

### Requirement: Migration preserves effective project access
The migration SHALL protect projects that were private by virtue of the old
personal-space membership restriction before removing that restriction.

#### Scenario: An existing personal Space grows
- **GIVEN** an existing personal Space containing org-visible projects
- **WHEN** the unified membership migration is applied and another member joins
- **THEN** the original projects require explicit project access
- **AND** the creator retains the same project IDs, content and Git locators.

### Requirement: Default selection is bound to the authenticated account
The system SHALL resolve an accessible default Space idempotently and SHALL
NOT accept a client-supplied user identity for provisioning.

#### Scenario: Concurrent first sign-in
- **GIVEN** an initialized account with no accessible Space
- **WHEN** multiple authenticated provisioning requests run concurrently
- **THEN** exactly one default Space is created and all requests return it.

### Requirement: A Space has one seat-based plan
The system SHALL authorize billing mutations by owner role and apply one plan
to the Space's purchased seats without using legacy personal/team labels.

#### Scenario: A single-person Space upgrades its plan
- **GIVEN** an owner and one purchased seat
- **WHEN** the owner confirms a valid Enterprise quote
- **THEN** the Space identity is unchanged and its published feature set follows that plan.
