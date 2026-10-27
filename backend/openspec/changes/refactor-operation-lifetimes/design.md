# Operation lifetime design

Canonical design and ASCII diagram: puppy-issues:document/puppyone/architecture/06-data-access-contracts.md#resource-and-operation-lifetimes.

Process clients and immutable caches are shared; authorization objects and bound grants are not cached globally. Read operations pass an explicit read-only session through stat/list/content/revision helpers. The session owns one existing GC pin, not a long SQL transaction. Independent requests never inherit a reader through ambient thread state.

infra/execution owns cooperative cancellation and joining. Interactive hosted reads have bounded running and waiting slots; transport timeouts still bound in-flight blocking calls. Durable write leases and results remain domain-owned. Workspace SSH remains a live filesystem path.

Adopt existing domains, not a universal service or RPC. Validate snapshot counts, revision consistency, authority separation, cancellation cleanup and existing regressions. Tests counting Python calls prove structural amplification only, not physical network latency.
