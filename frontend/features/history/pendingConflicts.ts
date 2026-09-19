import { listPendingConflicts, type PendingConflictSummary } from '@/lib/conflictApi';
import type { ConflictItem, PendingReviewItem } from '@/lib/needsActionRegistry';
import { workspaceKeys } from '@/lib/queryKeys';
import { useMemo } from 'react';
import useSWR from 'swr';

export function selectPendingItems(rows: PendingConflictSummary[], kind: string): (ConflictItem | PendingReviewItem)[] {
  return rows.filter(row => row.status === 'pending' && (kind === 'conflict'
    ? row.resolver_kind === 'human' || row.policy === 'manual_review'
    : row.resolver_kind === 'agent' && ['agent_review', 'agent_auto_resolve'].includes(row.policy)))
    .map(row => ({ kind: kind as 'conflict' | 'pending-review', id: row.pending_conflict_id, scope_path: row.scope_path, created_at: row.created_at, source: row }));
}

export function usePendingItems(projectId: string, kind: string, enabled: boolean) {
  const query = useSWR(enabled ? workspaceKeys.pendingConflicts(projectId) : null,
    () => listPendingConflicts(projectId), { refreshInterval: 30_000, refreshWhenHidden: false });
  const data = useMemo(() => query.data ? selectPendingItems(query.data, kind) : undefined, [query.data, kind]);
  return { data, error: query.error };
}
