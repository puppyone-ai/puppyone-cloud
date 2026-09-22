'use client';

import { useWorkspaceRouter } from '@/features/workspace/navigation';

import { useCallback, useEffect, useState } from 'react';


const EMPTY_PROJECT_OPEN_KEY_PREFIX = 'puppyone-empty-project-opened:';

function emptyProjectOpenKey(projectId: string): string {
  return `${EMPTY_PROJECT_OPEN_KEY_PREFIX}${projectId}`;
}

export function useEmptyProjectOpened({
  projectId,
  hasSetupParam,
}: {
  projectId: string;
  hasSetupParam: boolean;
}) {
  const router = useWorkspaceRouter();
  const [emptyProjectOpened, setEmptyProjectOpened] = useState(() => {
    if (hasSetupParam) return false;
    if (typeof window === 'undefined') return false;
    return window.localStorage.getItem(emptyProjectOpenKey(projectId)) === 'true';
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (hasSetupParam) {
      window.localStorage.removeItem(emptyProjectOpenKey(projectId));
      setEmptyProjectOpened(false);
      router.replace(`/projects/${projectId}/data`);
      return;
    }
    setEmptyProjectOpened(
      window.localStorage.getItem(emptyProjectOpenKey(projectId)) === 'true',
    );
  }, [hasSetupParam, projectId, router]);

  const openEmptyProject = useCallback(() => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(emptyProjectOpenKey(projectId), 'true');
    }
    setEmptyProjectOpened(true);
  }, [projectId]);

  return {
    emptyProjectOpened,
    openEmptyProject,
  };
}
