## ADDED Requirements
### Requirement: Persistent workspace file service
The system SHALL expose native files through a VM-owned service independent of a transport connection and Pi Loop.

#### Scenario: Disconnect and reconnect
- **WHEN** SSH disconnects after an admitted write
- **THEN** the service and file survive, the original binding is checked on reconnect, and uncertain writes are not replayed.

#### Scenario: Shared physical connection
- **WHEN** readers and watchers access one workspace in one backend process
- **THEN** they reuse a service connection with bounded independent subscriptions and cancellation does not close other requests.

#### Scenario: Compute epoch and idle pause
- **WHEN** a resource is paused, resumed, replaced or access is revoked
- **THEN** old channels lose authority, subscriptions do not renew activity, and recovery never silently selects another provider or directory.

#### Scenario: Native live edits
- **WHEN** Bash or an editor saves a file through atomic replacement
- **THEN** other admitted readers receive invalidation before Git publication while unsaved editor drafts remain intact.

#### Scenario: Provider extension
- **WHEN** another sandbox implements the existing service-connection contract
- **THEN** shared runtime and Desktop file behavior remain provider-independent and unsupported service capabilities fail explicitly.

#### Scenario: E2B attachment without activation
- **WHEN** an E2B workspace connects to its supervised file service
- **THEN** its adapter obtains transport credentials without implicit resume or timeout extension, validates the resource binding and disabled auto-resume policy, and uses the shared file protocol.
- **AND** isolated E2B acceptance does not change Fly deployment selection or introduce a fallback.
