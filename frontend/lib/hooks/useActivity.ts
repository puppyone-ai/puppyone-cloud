import useSWR from 'swr';
import {
  getProjectActivity,
  isActivityItemActive,
  type ActivityItem,
  type ActivityKind,
} from '@/lib/activityApi';

const DEFAULT_ACTIVITY_POLL_MS = 3000;

/**
 * Poll the unified activity feed for a project, optionally filtered to one
 * kind. Idle discovery remains slow but non-zero: another client can start a
 * job without a commit event. SWR suspends polling in hidden/offline tabs.
 */
export function useProjectActivity(
  projectId?: string | null,
  options?: { kind?: ActivityKind; activeOnly?: boolean; limit?: number },
) {
  const kind = options?.kind;
  const activeOnly = options?.activeOnly ?? false;
  const limit = options?.limit ?? 20;

  const { data, error, isLoading, mutate } = useSWR(
    projectId ? ['activity', projectId, kind ?? 'all', activeOnly, limit] : null,
    () => getProjectActivity(projectId!, { kind, activeOnly, limit }),
    {
      revalidateOnFocus: true,
      revalidateOnReconnect: true,
      refreshInterval: (latest) => {
        const items = latest?.items ?? [];
        return items.some(isActivityItemActive) ? DEFAULT_ACTIVITY_POLL_MS : 30_000;
      },
    },
  );

  const items: ActivityItem[] = data?.items ?? [];
  return {
    items,
    activeItems: items.filter(isActivityItemActive),
    isLoading,
    error,
    refresh: mutate,
  };
}
