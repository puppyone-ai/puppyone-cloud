'use client';

import { filesHref, useWorkspaceLocation, useWorkspaceNavigation } from '@/features/workspace/navigation';
import { useSessionValue } from '@/features/workspace/session';
import { useCallback, useEffect } from 'react';
import { usePathResolver } from './usePathResolver';

export function useDataRouteController({ projectId }: { projectId: string }) {
  const location = useWorkspaceLocation();
  const navigation = useWorkspaceNavigation();
  const [, setFilesHref] = useSessionValue('filesHref');
  const path = location?.projectId === projectId && location.view === 'files' ? location.path : [];
  const typeHint = location?.typeHint ?? '';
  const resolved = usePathResolver(projectId, path, typeHint);
  useEffect(() => {
    if (location?.projectId === projectId && location.view === 'files') setFilesHref(location.href);
  }, [location, projectId, setFilesHref]);
  const navigateTo = useCallback((nextPath: string[], hint?: string) => (
    navigation.navigate(filesHref(projectId, nextPath, hint), { from: location?.href })
  ), [projectId, navigation, location?.href]);
  return { ...resolved, path, serverTextContent: resolved.textContent, navigateTo };
}
