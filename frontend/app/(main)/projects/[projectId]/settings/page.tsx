'use client';

import { use, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { returnToFiles } from '@/features/workspace/navigation/routes';
import { PageLoading } from '@/components/loading';
import { useOpenProjectSettings } from '@/components/project/ProjectWorkspaceShell';
import { useSessionValue } from '@/features/workspace/session';

/** Old settings bookmarks open the dialog over Files, not a separate project view. */
export default function ProjectSettingsRoute({ params }: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = use(params);
  const router = useRouter();
  const openSettings = useOpenProjectSettings();
  const [filesHref] = useSessionValue('filesHref');

  const handled = useRef(false);
  useEffect(() => {
    if (handled.current) return;
    handled.current = true;
    openSettings();
    router.replace(returnToFiles(projectId, filesHref), { scroll: false });
  }, [filesHref, openSettings, projectId, router]);

  return <PageLoading variant='fill' label='Opening project settings' />;
}
