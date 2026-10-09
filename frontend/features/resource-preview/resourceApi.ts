import { get } from '@/lib/apiClient';

export interface ResourceEntry {
  name: string;
  path: string;
  type: string;
  mime_type: string | null;
  size_bytes: number | null;
  git_mode: string | null;
  path_bytes_b64: string | null;
}

export interface WorkspaceResource {
  kind: 'file' | 'directory';
  path: string;
  entry: ResourceEntry;
  entries: ResourceEntry[];
  repository_revision: { tree_oid: string; expected_oid: string | null; generation: number };
  next_cursor: string | null;
  preview: 'text' | 'image' | 'unavailable';
  content_text: string | null;
  content_base64: string | null;
  truncated: boolean;
}

export function readWorkspaceResource(projectId: string, path: string, cursor?: string, pathBytes?: string) {
  const query = new URLSearchParams(pathBytes ? { path_bytes_b64: pathBytes } : { path });
  if (cursor) query.set('cursor', cursor);
  return get<WorkspaceResource>(`/api/v1/content/${encodeURIComponent(projectId)}/resource?${query}`, {
    cache: 'no-store',
  });
}
