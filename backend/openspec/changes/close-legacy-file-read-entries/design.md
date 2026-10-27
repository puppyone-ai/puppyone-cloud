# Design

Resource ownership remains in the existing layers:

    HTTP / indexing job
      -> canonical authorization -> ProjectGrant
      -> shared ProductOperationAdapter.for_grant
      -> one operation-owned ProductReadSession
         -> serialized off-loop native reads -> PG read pin / S3

`file_operations` owns an async read-session bridge for jobs which interleave
bounded native queries with async embedding/vector work. It uses the existing
read admission and owned executor for each blocking query, not while waiting for
embedding. A session serializes access, joins in-flight reads before mandatory
cleanup and cannot be used after close. Native pin renewal remains active and
expired pins fail closed. User access is separately bounded by task-local
`platform/authorization/lifetime.py`: after 30 seconds the next protected read
or index write calls canonical `authorize_fresh`, bypassing an inherited HTTP
facts cache. Failed revalidation stops work. This is checkpoint-based revocation,
not cancellation of an external call already in flight. Existing job deadlines
and write leases remain.
No authority is stored in a process singleton or ambient context.

Search owns indexing orchestration. File content is consumed sequentially from
one snapshot; source/embedding/primary vector-write failures fail the task instead of becoming
empty content or successful partial indexing. This does not introduce atomic
vector-index publication; existing per-file upserts remain and failed jobs are
reported as failed.
Existing repairable chunk-to-vector back-reference updates and progress reporting
remain best-effort; they are not primary content or vector publication.

Agent config retains protocol/visibility rules. Its metadata query helper receives
an explicit ProjectGrant and a process-injected adapter. It has no authorization
RPC or fallback service identity.

Archive has a non-importable source extension and is outside `src`. No redirect,
compatibility handler or feature flag preserves the retired node endpoints.
Other internal billing, table and MCP runtime contracts remain active.
