// API response shapes for `GET /api/v1/projects/:id/dashboard`.
// Keep these aligned with `backend/src/platform/project/dashboard_router.py`.

import type { TreeEntry } from '@/lib/contentTreeApi';

export interface DashboardProject {
  id: string;
  name: string;
  description: string | null;
}

export interface DashboardNodeCounts {
  total: number;
  folders: number;
  files: number;
}

/** Backend `connections.direction` (connections_direction_check constraint).
 *  - `inbound`       — data flows from external source INTO ContextBase
 *  - `outbound`      — data flows from ContextBase OUT to consumer (agent / mcp / sandbox)
 *  - `bidirectional` — both ways (Git Remote: local repo is source AND mirror)
 *  Kept loose as `string | null` on the wire because incomplete rows may omit it;
 *  use `getApDirection()` from `./constants` to normalize before consuming. */
export type ApDirection = 'inbound' | 'outbound' | 'bidirectional';

export interface DashboardConnection {
  id: string;
  scope_id?: string | null;
  provider: string;
  name: string | null;
  path: string | null;
  direction: string | null;
  status: string;
  /** Always null on list responses; machine credentials are one-time reveal. */
  access_key: string | null;
  has_credential?: boolean;
  credential_hint?: string | null;
  scope_mode?: 'r' | 'rw' | null;
  trigger: any;
  last_synced_at: string | null;
  error_message: string | null;
  created_at: string | null;
  /** Per-day invocation counts for the last 14 days (oldest → newest),
   *  sourced from `sync_runs` on the backend. Empty/zero for APs with no runs. */
  usage_buckets?: number[];
}

export interface DashboardTool {
  id: string;
  name: string;
  type: string | null;
  index_status: string | null;
}

export interface ProjectDashboard {
  project: DashboardProject;
  nodes: DashboardNodeCounts;
  connections: DashboardConnection[];
  tools: DashboardTool[];
  uploads: { id: string; status: string }[];
}

// Local view-model: nested tree built from the flat `TreeEntry[]` the API
// returns. Lives here (not in `contentTreeApi`) because the nesting strategy
// is owned by Home — the file explorer page builds its tree differently.
export interface TreeNode {
  entry: TreeEntry;
  children: TreeNode[];
}
