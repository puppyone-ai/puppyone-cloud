# Design

SSH is an incoming adapter. An authenticated owner issues a public-key grant
bound to their existing workbench ID and generation. The grant authorizes full
shell access to this private working directory, never Project administration or
another user's copy. One grant admits one SSH session; reconnect requires a new
grant. SSH forwarding, agent forwarding and SFTP subsystems are disabled.

The Workspace kernel reserves an immutable process GID and resource identity in
PostgreSQL before opening a provider channel. Shell, exec, PTY resize and binary
I/O use one common framed protocol over existing Provider transports. The
controller drops privileges before starting the shell and preserves ordinary
filesystem semantics. No Git commit is required for editor visibility.

The gateway periodically rechecks the grant, Project permission, workbench
binding and runtime billing. Revocation, expiry, disconnect and failed renewal
stop the exact process group. A local controller lease also stops work on gateway
loss. A durable reaper retries cleanup; elapsed time alone never releases
occupancy. Active or unconfirmed connections block pause, retirement and deletion.

Host keys are operator-owned persistent keys; no permissive host-key fallback is
introduced. The authenticated API supplies the public host key. Public endpoint
configuration and matching runtime artifacts are required to activate the feature.
