## Decisions

Interactive ownership is `(user_id, project_id)`. Sessions own conversations and task identities; the workspace owns compute, native files and its Git branch. Automation execution remains separately owned.

Provider defaults choose new resources. All attach, wake, cleanup and file operations use the persisted provider, artifact and generation. Unknown providers or missing capabilities fail before resource mutation; a provider timeout never means files disappeared.

Loop, Execution and Filesystem remain injected independently. Native files live inside the current workspace provider. Browser traffic goes through an authorized backend FS API; it never receives provider credentials. Watches invalidate reads and do not keep idle compute running. Saved Agent writes appear before Git publication. Git history remains canonical published history, not a live-file transport.

E2B uses SDK bidirectional commands and pause/snapshot semantics. Fly uses a private Machine and persisted disk with an exact-machine SSH channel. Fly volumes are not represented as E2B snapshots. Docker remains the local acceptance adapter. The production lifecycle must validate provider readiness before accepting shared tasks.

Workspace tasks must share native file ownership (including mode 0600 and atomic rename) while task termination identifies only the admitted task and its descendants. Control metadata and Git transport credentials stay outside the writable tree.

## Validation and rollout

Exercise real native filesystem/process behavior, provider contract parity, authorization, persisted binding after default switches, reconnect, stale generations and dirty-editor conflicts. Run the repository regression gates and populated database upgrade rehearsal before target-branch merge. Report source, local integration, hosted activation and paid-provider acceptance separately.
