# Design

Access owns durable lifecycle, Authorization owns decisions, adapters own wire
authentication, and Workspace owns resource leases and process cleanup. Human
login remains unchanged. Database functions serialize approval, credential
rotation, admission and revocation; no browser can mutate these tables directly.

Registrations use a stable ID and a client-generated renewal secret. The client
stores secrets; only keyed hashes enter SQL. Rotation is single-flight and
idempotent by request ID and next-secret hash. Reuse with a different request
denies and suspends the registration. This is a Puppyone connector credential
exchange, not a claim to implement the complete MCP OAuth authorization server.

Invitations admit requests only. Verified human identity binds a registration
to a user or Organization. Approval requires current Project administrative
authority and, for a private Workspace, its actual owner. Durable grants bind
one explicit resource and approved channels. Repository and Workspace rights
cannot be exchanged. Native Scope remains unavailable.

Protocol credentials retain their existing format and storage. New credentials
reference the parent grant. Existing credentials without that link retain only
their current authority. SSH login tickets are single-use; parent-grant sessions
renew independently of ticket admission expiry. The Workspace connection ID
is shared with the access-session record, without duplicating execution state.

Publication checks parent authorization while holding the grant/registration
locks. Revocation records state and a durable event atomically, denies future
admission immediately, and closes active sessions through the existing cleanup
owner; finite leases bound disconnected workers. Completed writes are not undone.

Web features and the standalone CLI call the same checked management APIs.
The browser holds only an invitation/application; the CLI owns private renewal
state. Invitation origins cannot redirect credentials to another API. The CLI
serializes rotations and writes an atomic recovery journal before exchange.
Routes compose the UI and own navigation; existing Access panel ownership stays
unchanged. Providers do not gain an authorization implementation.

Tests use isolated PostgreSQL and production adapters. Real Fly acceptance uses
disposable workspaces and an already-published compatible runtime image. It does
not require a hosted migration, remote customer data, mail, changing existing
Fly Machines, or publishing the candidate package.
