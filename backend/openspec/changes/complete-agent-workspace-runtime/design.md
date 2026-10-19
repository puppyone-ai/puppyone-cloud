## Decisions

Interactive ownership is `(user_id, project_id)`. Sessions own conversations and task identities; the workspace owns compute, native files and its Git branch. Automation execution remains separately owned.

Provider defaults choose new resources. All attach, wake, cleanup and file operations use the persisted provider, artifact and generation. Unknown providers or missing capabilities fail before resource mutation; a provider timeout never means files disappeared.

Loop, Execution and Filesystem remain injected independently. Native files live inside the current workspace provider. Browser traffic goes through an authorized backend FS API; it never receives provider credentials. Watches invalidate reads and do not keep idle compute running. Saved Agent writes appear before Git publication. Git history remains canonical published history, not a live-file transport.

E2B uses SDK bidirectional commands and pause/snapshot semantics. Fly uses a private Machine and persisted disk with an exact-machine SSH channel. Fly volumes are not represented as E2B snapshots. Docker remains the local acceptance adapter. The production lifecycle must validate provider readiness before accepting shared tasks.

Workspace tasks must share native file ownership (including mode 0600 and atomic rename) while task termination identifies only the admitted task and its descendants. Control metadata and Git transport credentials stay outside the writable tree.

## Validation and rollout

Exercise real native filesystem/process behavior, provider contract parity, authorization, persisted binding after default switches, reconnect, stale generations and dirty-editor conflicts. Run the repository regression gates and populated database upgrade rehearsal before target-branch merge. Report source, local integration, hosted activation and paid-provider acceptance separately.

## Provider and I/O boundary

The user approved the provider-only extension boundary and requested documentation first, implementation, and fast real-provider I/O verification. Preserve Pi and the existing module layout. Providers return bound connection handles and explicit retention/transition results; common runtime code does not inspect supplier names or SDK handles. File channels are bounded process-local caches keyed by immutable resource binding and compute epoch. Each operation reauthorizes and renews actual activity; watch keepalive does not. Disconnect, cancellation, replacement and suspend invalidate channels. Mutations are never replayed after an ambiguous reply. Provider HTTP clients use bounded connection pools.

Measure cold start, first connection, and warm file operations separately on E2B and Fly. Small-file warm p50 <= 300 ms and p95 <= 1 s are acceptance targets, not assumed outcomes. Publish measured sample counts and network placement.

The file channel reuses an unprivileged native helper and negotiates optional bounded deflate for read payloads. The public file shape and original content revision remain unchanged; older plain responses remain accepted. Transport cache bounds and idle eviction are deployment settings, independent of compute activity.
