## Implementation
- [x] Implement the registered provider and bounded async exec transport.
- [x] Package and bootstrap the existing immutable worker on persistent storage.
- [x] Verify ownership, pause/resume, cleanup, transport and configuration contracts.
- [x] Document activation, preservation semantics and hosted acceptance limits.
- [x] Run applicable checks and record the results.

## Verification notes
- Backend regression after default/cutover changes (`not e2e and not integration
  and not network`): 3562 passed, 956 skipped, 420 deselected.
- 70 provider/cutover/worker/configuration tests passed. After the installer
  compatibility and stdin transport fixes, 28 provider/archive tests and 13
  config/archive tests passed.
- 22 real PostgreSQL/PostgREST integration tests passed for workspace ownership,
  durable opening and worker dispatch.
- The existing native file acceptance passed all 4 scenarios against the new
  linux/amd64 image, including actual Agent/Bash saves, another Agent/terminal
  reading them, editor updates, file watches, conflicts and path confinement.
- The read entrypoint audit passed after reviewing the local artifact `stat` site.
- Changed Python files passed Ruff; the worker passed Node syntax checking;
  bootstrap/build/control scripts passed Python compilation; OpenSpec strict
  validation passed.
- Docker recovered. The linux/amd64 worker image built and the 71,705,397-byte
  bundle exported successfully. Bundle SHA-256:
  `74d411bada465debba9aecf9136df5d3a460dd70f57b029eb8f4da1a2ba5b2f7`.
- The actual bundle passed isolated Linux-container acceptance for installation,
  retry, root socket ownership/UID isolation, firewall setup, native file saves,
  hold create/release, file preservation across service restart/remount, and
  rejecting artifact replacement. See `sprites-local-receipt.json`.
- Container acceptance exposed an older-Python tarfile incompatibility. The
  installer now extracts regular files before confined links, rejects traversal
  and special entries, and receives code over stdin rather than a long exec URL.
- The supplied full Sprites token authenticated successfully. Initial hosted
  acceptance exposed the platform's read-only management socket; chmod/chown
  cannot seal it. An owned real-Sprite probe verified a root-only bind alias plus
  masking the public path, including denied task access and working external
  exec. All failed-run/probe resources were deleted and absence verified. The
  full product and cold-wake scenario must pass with the rebuilt bundle below.
- Rebuilt bundle `6035238f9bc6fcf69d3c5eb27f4e07becf3a78e585ade4f7b1e16417cbbc6a93`
  passed actual read-only-socket container acceptance, including private-alias
  isolation and restoring mounts before reading saved files. The 42 focused
  provider/archive/default/configuration regressions passed.
- The rebuilt bundle passed real Agent/editor live writes, watch, task isolation
  and Git publication. A second task exposed stale provider state observations:
  a separate held/running-process probe alternated between running and an old
  cold creation record. Attachment now retries metadata only; pause cannot skip
  hold release based on a stale record. Cold acceptance also requires a changed
  kernel boot ID after reopening. All probe and failed-scenario resources were
  deleted and absence verified.
- Complete regression on the read-only-socket fix: 3570 passed, 956 skipped,
  420 deselected. Metadata-handling changes have additional focused regressions.
- The 46 metadata/provider/archive/default/configuration tests passed. The next
  real run passed consecutive Agent reuse and reached verified warm pause. Its
  natural-cold wait was stopped and the owned resource deleted: the platform has
  no bounded cold-eviction deadline. Final acceptance separately covers warm
  reopen and official checkpoint restore of an exact paused fixture, including
  proof that an unrelated process is lost before reopening the same saved files.
  Natural cold eviction remains an explicit optional soak check, not a claim
  inferred from intermittent cold metadata. This does not add checkpoint usage
  to the runtime's normal lifecycle.
- Read-only preflight with Qubits' actual local configuration passed for Sprites
  authentication/artifact and the authorization/workspace database RPC contracts.

## Enablement gates
- [x] Build and export the actual worker bundle successfully.
- [ ] Run hosted acceptance, including task management-socket isolation, native
  file edits and warm pause/reopen preservation; separately verify recovery after
  provider-level process reset from the exact saved filesystem.
- [x] Make Sprites the source/example default and implement retained-provider
  admission, Agent capability checks, open dispatch and maintenance for old Fly
  bindings. Focused cutover/provider regressions: 70 passed.
- [ ] Run the completed integration checks and merge the verified commit into qubits.

Review correction: management-socket mode alone did not enforce isolation, and
the real platform mounts it read-only. Startup now uses root-only mount traversal
and masks the public socket without modifying the platform inode. Container
acceptance emulates the read-only mount and remounts it across service restart.
