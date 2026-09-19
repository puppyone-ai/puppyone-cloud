'use client';

import { use, useEffect } from 'react';
import { useRouter } from 'next/navigation';
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

  useEffect(() => {
    openSettings();
    router.replace(filesHref || `/projects/${projectId}/data`, { scroll: false });
  }, [filesHref, openSettings, projectId, router]);

  return <PageLoading variant='fill' label='Opening project settings' />;
}
