# Workbench ownership

The database owns resource generation and lifecycle transitions. Runs retain
their own execution lease/fence and conversation, but cannot pause, destroy or
restore a whole shared computer. Shared files are live state, including changes
made by another Agent after a conversation checkpoint. Conflicts remain the
user's responsibility. No per-Agent branch or mandatory writer queue is added.

The trusted sandbox controller receives a server-assigned execution UID; each
task uses a distinct UID and the shared workbench group. This bounds stop/freeze
to that task even when tools detach descendants. User terminals retain their own
UID. Group-writable files permit normal shared-directory editing. Provider
credentials remain on the host. The legacy Session protocol remains supported
while the shared contract is introduced explicitly and tested.

Git metadata operations use the existing stock Git path. Git publication remains
bounded by the Version Engine grant, ref CAS and durable operation receipt.
Conversation recovery never implies permission to restore a shared filesystem.
Resource deletion belongs to the lifecycle owner after its durable CAS claim.

Schema changes are Expand-only with representative populated-upgrade tests;
historical migrations are immutable. No hosted database acceptance is inferred
from a local fixture. Compatibility and release evidence are recorded separately.

