import useSWR from 'swr';
import { workspaceEntryPath } from '@puppyone/cloud-core';
import { readWorkspaceResource, type ResourceEntry } from './resourceApi';

/** Preview and tree subscribe to one identity-scoped read, including negative results. */
export function useResource(userId: string, projectId: string, path: string | null, bytes?: string, cursor?: string) {
  const locator = path === null ? null : workspaceEntryPath(projectId, { path, path_bytes_b64: bytes ?? null, type: 'file' });
  return useSWR(locator === null ? null : ['workspace-resource', userId, locator, cursor ?? ''],
    () => readWorkspaceResource(projectId, path!, cursor, bytes),
    { shouldRetryOnError: false, revalidateOnFocus: false, keepPreviousData: false });
}
export const entryIdentity = (entry: ResourceEntry) => entry.path_bytes_b64 ?? entry.path;
export const visibleEntries = (entries: ResourceEntry[]) => entries.filter(entry => !['.DS_Store', 'Thumbs.db', '.git'].includes(entry.name));
export const defaultPreview = (entries: ResourceEntry[]) => visibleEntries(entries).find(entry => entry.type !== 'folder' && /\.(md|markdown|txt|json|csv)$/i.test(entry.name));
