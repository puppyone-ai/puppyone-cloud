## ADDED Requirements
### Requirement: Workspace kernel and access adapters
The system SHALL keep workspace lifecycle, native files and provider implementations
in platform/workspace. Access adapters SHALL call the shared kernel; the kernel
SHALL NOT import Access or HTTP presentation implementations.

#### Scenario: SSH connection
- **WHEN** the existing Scope SSH endpoint connects or revokes a caller
- **THEN** the SSH adapter authenticates and renders connection information
- **AND** the Workspace kernel owns resource acquisition, state and cleanup
- **AND** the existing URL and Scope ownership remain unchanged

#### Scenario: Live file access
- **WHEN** the editor or Agent uses native Workspace files
- **THEN** both use the shared Workspace filesystem contract
- **AND** file access does not require importing an Agent loop implementation

#### Scenario: Provider replacement
- **WHEN** a supported runtime provider is selected
- **THEN** lifecycle policy remains in Workspace and supplier code remains in providers
- **AND** no SSH adapter instantiates supplier SDKs or changes resource binding
