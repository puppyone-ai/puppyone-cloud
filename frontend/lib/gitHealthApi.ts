import { get, post } from '@/lib/apiClient';

export type GitViewHealth = 'empty' | 'healthy' | 'history_degraded' | 'current_corrupt';

export interface GitHealthAction {
  type: string;
  label: string;
}

export interface GitViewHealthPayload {
  project_id: string;
  scope_path: string;
  scope_excludes: string[];
  health: GitViewHealth;
  git_head: string;
  canonical_head: string;
  history_cut: boolean;
  git_usable: boolean;
  clone_usable: boolean;
  fetch_usable: boolean;
  push_usable: boolean;
  read_only: boolean;
  can_rebuild: boolean;
  reason: string;
  recommended_actions: GitHealthAction[];
}

/** Health for the project-root Git remote — the user-visible mirror of the
 * canonical project tree. */
export function getGitProjectHealth(projectId: string): Promise<GitViewHealthPayload> {
  return get<GitViewHealthPayload>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/git-view/health`,
  );
}

export interface GitCacheRebuildVariant {
  view_id: string;
  project_id: string;
  scope_path: string;
  scope_excludes: string[];
  history_mode: 'full' | 'receive-boundary';
  blob_mode: 'included' | 'omitted';
  head: string;
}

export interface GitCacheRebuildResponse {
  variants: GitCacheRebuildVariant[];
}

/** Drop and rewarm the per-view Git transport cache from canonical Version
 * Engine facts. Both cache variants (full-history-with-blobs for clone/fetch
 * and receive-boundary-without-blobs for push advertisement) are rebuilt in
 * one call so the next request hits a warm cache regardless of direction.
 * Requires Project management access. */
export function rebuildGitProjectCache(projectId: string): Promise<GitCacheRebuildResponse> {
  return post<GitCacheRebuildResponse>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/git-view/rebuild-cache`,
    {},
  );
}
