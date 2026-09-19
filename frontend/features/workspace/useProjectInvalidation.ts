'use client';

import { useEffect, useRef } from 'react';
import { useSWRConfig } from 'swr';
import { useCommitUpdates } from '@/contexts/VersionWebSocketContext';
import { changedFolders, isCommitInvalidationKey } from '@/lib/queryKeys';

/** Project lifetime, not view lifetime. Coalesce replay bursts; never invalidate
 * immutable commit previews or unrelated access/configuration metadata. */
export function useProjectInvalidation(projectId: string) {
  const { mutate } = useSWRConfig();
  const folders = useRef(new Set<string>());
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useCommitUpdates(event => {
    for (const folder of changedFolders(event.changed_files ?? [])) folders.current.add(folder);
    if (timer.current) return;
    timer.current = setTimeout(() => {
      const affected = folders.current;
      folders.current = new Set();
      timer.current = undefined;
      void mutate(key => isCommitInvalidationKey(key, projectId, affected)).catch(() => {});
    }, 100);
  });
  useEffect(() => () => { clearTimeout(timer.current); folders.current.clear(); timer.current = undefined; }, [projectId]);
}
