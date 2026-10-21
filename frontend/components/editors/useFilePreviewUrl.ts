'use client';

import { useEffect, useState } from 'react';
import { getInlinePreviewUrl } from '@/lib/contentTreeApi';
import { useWorkspaceFiles } from '@/lib/hooks/useWorkspaceFiles';
import { useAuth } from '@/contexts/SupabaseAuthProvider';

/** One lifecycle for native file previews in Workspace and resource links.
 * Images release their object URLs; media keep browser-native range requests.
 * A changed identity never renders the previous file while its request is pending.
 */
export function useFilePreviewUrl(projectId: string, path: string, kind: 'blob' | 'inline', pathBytes?: string): { url?: string; error?: string } {
  const { userId } = useAuth();
  const {files, identity, revision, workspace, ready} = useWorkspaceFiles();
  const live = Boolean(workspace);
  const key = JSON.stringify([userId, projectId, pathBytes ?? path, kind, identity, revision]);
  const [state, setState] = useState<{ key: string; url?: string; error?: string }>();
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    const options = { pathBytes, signal: controller.signal };
    const load = async () => {
      try {
        if (!ready) return;
        const value = kind === 'blob' || live
          ? await files.fetchRawBlob(projectId, path, options)
          : await getInlinePreviewUrl(projectId, path, options);
        if (controller.signal.aborted) return;
        const url = typeof value === 'string' ? value : (objectUrl = URL.createObjectURL(value));
        setState({ key, url });
      } catch (error) {
        if (!controller.signal.aborted) setState({ key, error: error instanceof Error ? error.message : String(error) });
      }
    };
    void load();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [key, projectId, path, pathBytes, kind, files, live, ready]);
  return state?.key === key ? state : {};
}
