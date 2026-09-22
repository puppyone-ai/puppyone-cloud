"""Persistent database identifiers for the Version Engine boundary.

The runtime architecture is Git-native and product-facing code should use
Version Engine names. These constants are the canonical schema contract;
temporary ``mut_*`` compatibility objects are only for mixed-version rollout.
"""

COMMIT_HISTORY_TABLE = "version_commits"
SCOPE_STATE_TABLE = "version_scope_state"
VERSION_INDEX_TABLE = "version_view_commits"
VERSION_OUTBOX_TABLE = "version_outbox"
OBJECT_LOCATIONS_TABLE = "version_object_locations"
CONFLICTS_TABLE = "version_conflicts"

PROJECT_ROOT_HASH_COLUMN = "version_root_hash"
GITHUB_SYNC_VERSION_COLUMN = "version_commit_id"

PUBLISH_PROJECT_UPDATE_RPC = "publish_version_project_update"
PROJECT_WRITE_STATE_RPC = "get_version_project_write_state"
PROJECT_HISTORY_REFS_RPC = "get_version_project_history_refs"
CLAIM_OUTBOX_RPC = "claim_version_outbox_batch"
COMPLETE_OUTBOX_RPC = "complete_version_outbox"
FAIL_OUTBOX_RPC = "fail_version_outbox"
