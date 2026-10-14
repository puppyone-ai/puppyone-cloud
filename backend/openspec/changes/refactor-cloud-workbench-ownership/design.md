# Workbench ownership

## Product and authority

A selected workspace is one cloud computer: one current resource generation,
one live worktree and one current branch, shared by the owning user's Chats,
terminal, preview and file access. A workspace belongs to exactly one user;
Project membership or administration does not permit entering another user's
live workspace. Published Project content is shared through existing grants/Git,
not through shared compute. Starting a Chat creates neither workspace nor sandbox.
A running task retains its admitted binding when the user changes UI selection.
Conflicts are ordinary user-managed filesystem/Git conflicts, not a new merge
coordinator or mandatory file-write queue.

The database owns resource generation and lifecycle transitions. Runs retain
execution leases/fences, conversation and publication candidates, but cannot
pause, destroy or restore a whole shared computer. Conversation recovery never
rewinds shared files. Resource replacement requires confirmed cleanup and a
recoverability decision, not simply an expired Run lease.

## One execution contract

The user approved the clean pre-launch cutover on 2026-10-14: old Chats need not
be migrated or retained. New Runs require the selected workbench; missing or
invalid selection is rejected, never interpreted as permission to allocate a
Session sandbox. Keep the existing dispatcher, supervisor, authorization and
native Git authority. Remove the old runtime composition at cutover, rather
than growing optional adapters and permanently maintaining two schedulers.
This does not authorize deleting project files or abandoning provider resources.
Historical migrations are immutable; cutover SQL is forward-only and tested
against populated databases. Deployment coordination remains separate.

## Workspace provisioning

Platform workspace creation, not Chat submission, owns allocation: authorize the
owner/Project, persist an idempotent identity and allocation intent, provision one
resource, initialize the directory/Git once, then acknowledge readiness through
resource generation/version/operation CAS. Return preparing/failed until readiness
is confirmed. Repeated creation or a lost response reconciles that same operation;
it does not allocate one machine per Chat or silently replace a unique copy.

Opening/waking a paused workspace is a workspace operation. Tasks only borrow an
already-running, authorized connection. A task cannot repair missing, paused,
retired or lost compute by creating/resuming it. Resource retirement leaves the
workspace identity and responsibility intact; replacement requires its explicit
lifecycle operation and recoverability checks. No Session owns provider lifetime.

## Task and file boundaries

Resource lifecycle and task transport are separate capabilities. A task receives
an already-admitted connection and can stop only its execution. It does not
inherit resource creation/pause/deletion as methods that merely throw at runtime.
Provider credentials and trusted control state remain outside user-writable paths.
The selected target uses one ordinary-user filesystem identity for the owner and
all their Agents in this workspace. Private-mode saves remain accessible to that
same identity. Task process domains (including detached descendants) provide stop
isolation separately; concrete provider support must be tested. Different task
UIDs are not a product security boundary between this user's Agents. Root/service
control identity remains separate; this never permits cross-user workspace access.

The merged distinct-task-UID/shared-group implementation is NOT final acceptance:
2026-10-14 Linux reproduction showed that a 0600 temporary-file rename is not
readable by another Agent of the SAME user. This is not a request for cross-user
sharing; it is a defect introduced by the task UID mechanism. The initial helper-only Git probe missed the wrapper's
final chmod to 0660; the full-controller regression then reproduced the actual
bug: other Git clients fail during the temporary 0600 inspection window. The
new shared Git control view fixes that window and config rewriting, but does
not fix the ordinary 0600 file ownership problem. Process ownership must not
silently turn into file isolation. The final provider
mechanism must prove shared-file permission behavior, detached-child cleanup,
controller death and protection from stale cleanup. Do not repair this with a
recursive chmod, broad host privilege, per-Agent file copies or a filesystem
synchronization service. Provider support must be verified before activation.

## Native publication

The workbench target ref is selected explicitly; repository HEAD remains the
existing display/default-clone source, not another pointer table. A non-primary
workbench advances only its own ref. If HEAD explicitly points to that ref,
existing native symbolic-ref semantics provide following. Changing display
selection is an explicit authorized native ref transaction, never a side effect
of an Agent push. Do not modify Version Engine read/cache authority.

Capture generation, target ref and expected OID before publication. Explicit
workbench refs do not depend on unrelated changes to repository HEAD. The native
publication validates the exact captured candidate/ref and applies ref CAS with
the original operation key. A later local commit does not change the old Run's
candidate. Local branch switching must not silently redirect the publication.

Shared Git now uses a root-owned read-only control view containing fixed config
and a neutral unborn HEAD used only as command input, not a published ref.
Refs, objects, packed refs and reflogs alias the user's native metadata; the
index path is the original shared index. No worktree, objects or ref database is
copied. Git runs without root privilege: lifecycle initialization uses the file
user; task Git still uses the existing task UID pending the A2 isolation change.
Protected control state resides under root-owned `/run`, not the ordinary user's
writable `/tmp` parent. Commands select the admitted
ref/OID explicitly and check the user's actual HEAD. Native commit-tree and
update-ref CAS capture a candidate without running user hooks or changing files.

Trusted publication neither rewrites user config nor evaluates its includes,
helpers, filters or URL rewrites. The immutable control view—not the service-only
lock—separates executable Git configuration from user metadata. Linux tests
exercise SHA-1/SHA-256, ordinary terminal Git during inspection, concurrent hostile
config, control-config write denial, stock Git HTTP fetch/push and original
candidate retries. These process tests are not provider lifecycle acceptance.

## Interaction, activity and recovery

File/watch, ordinary-user PTY and preview use the same live filesystem and are
bound to actor/project/workbench/generation and current authorization. Sealed
policy denies user interaction; interactive policy does not implicitly enable
internet egress or public ingress. Revocation and generation changes terminate
old capabilities. Published/history reads do not wake compute.

One workbench owns compute accounting and effective activity leases, including
no-Run human/background activity. Model usage remains Run-bound. Passive polling
is not activity. Provider usage, platform charges and pause-holding fees are
separate facts, based on the provider contract.

Cleanup is restricted to registered disposable scratch, not all /tmp or live
control/socket directories. Record connect and actual readiness separately;
unavailable physical snapshot bytes are not zero or disk-usage estimates.
Template+Git reconstruction requires durable save receipts and confirmation that
unique files, ignored dependencies and memory may be discarded. Unknown is not
recoverable. No paid resources are authorized for current verification.
