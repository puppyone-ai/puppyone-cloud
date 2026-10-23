# Close legacy file read entries

The user approved retiring the complete internal node/file HTTP surface,
composing search index reads into the shared operation lifecycle, and correcting
Agent metadata grant propagation. These implement the accepted lifecycle design;
no further approval or SQL migration is needed.

- Archive `/internal/nodes/*` source outside executable packages and remove all
  eight routes, their authorization registrations and obsolete active tests.
- Own one authorized native snapshot throughout an indexing task. Read one file
  at a time off-loop, preserving pin renewal, task lease, cancellation and error
  propagation. Do not retain all repository file contents in memory.
- Pass the Agent-read ProjectGrant to the metadata query and resolve unique
  paths under a single snapshot. Missing paths differ from unavailable storage.
- Verify absence of retired HTTP routes and archive imports, actual reader call
  budgets, version consistency, isolation and failure cleanup.
