'use client';

import { use, useEffect, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { returnToFiles } from '@/features/workspace/navigation/routes';
import { PageLoading } from '@/components/loading';
import { useProjectSession, useSessionValue } from '@/features/workspace/session';

/**
 * Compatibility adapter for bookmarks and older in-product links.
 * Access is project chrome now: open the shared right sidebar and restore the
 * last Files URL instead of mounting a third top-level project view.
 */
export default function AccessRouteAdapter({
  params,
}: {
  readonly params: Promise<{ projectId: string }>;
}) {
  const { projectId } = use(params);
  const router = useRouter();
  const searchParams = useSearchParams();
  const [filesHref] = useSessionValue('filesHref');
  const openPanel = useProjectSession(state => state.openPanel);

  const handled = useRef(false);
  useEffect(() => {
    if (handled.current) return;
    handled.current = true;
    const target = searchParams?.get('target') ?? undefined;
    const endpoint = searchParams?.get('ap') ?? undefined;
    const create = searchParams?.get('create');
    const hasPath = searchParams?.has('path') ?? false;
    const path = hasPath ? (searchParams?.get('path') ?? '') : undefined;
    const isCreate = create === 'share-with-ai' || create === '1' || create === 'true';

    openPanel({
      type: 'access_list',
      view: isCreate ? 'create' : target || endpoint || hasPath ? 'detail' : 'overview',
      selectedTargetKey: target,
      accessEndpointId: endpoint,
      nodeId: path,
    });
    router.replace(returnToFiles(projectId, filesHref), { scroll: false });
  }, [filesHref, openPanel, projectId, router, searchParams]);

  return <PageLoading variant='fill' label='Opening access' />;
}
