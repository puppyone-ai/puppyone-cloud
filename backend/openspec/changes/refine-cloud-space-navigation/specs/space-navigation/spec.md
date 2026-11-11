## ADDED Requirements

### Requirement: Cloud-only Space selection
The Desktop SHALL retain registered local projects across Space changes and SHALL scope cloud projects by their owning Space. Switching SHALL be window-local and live at the Sidebar bottom.

#### Scenario: Switch spaces
- **WHEN** a user changes the selected Space
- **THEN** its cloud projects replace the previous cloud list while all registered local projects remain visible

#### Scenario: Signed out
- **WHEN** no account is signed in
- **THEN** This Mac stays usable without fabricating a cloud Space or reading another account's projects

### Requirement: One Space classification
The public organization projection SHALL identify every admitted organization as a Space regardless of legacy provisioning columns. Space and project memberships SHALL continue to authorize their existing roles.

#### Scenario: Historical personal space grows
- **WHEN** an existing personal-origin space gains more members
- **THEN** its ID, project ACLs and bill remain stable and its public kind is space
