'use client';

import { useSessionValue } from '@/features/workspace/session';
import { useEffect } from 'react';

interface HistorySnapshot {
  loaded: boolean;
  scopeOptions: readonly { scope: string }[];
  actorOptions: readonly { type: string }[];
  filteredCommits: readonly { commit_id: string }[];
  headCommitId: string;
  needsActionSelected: boolean;
}

/** Reconcile only against a received snapshot, not the loading placeholder. */
export function useHistoryReconciliation({
  loaded, scopeOptions, actorOptions, filteredCommits, headCommitId, needsActionSelected,
}: HistorySnapshot) {
  const [scope, setScope] = useSessionValue('historyScope');
  const [actor, setActor] = useSessionValue('historyActor');
  const [commit, setCommit] = useSessionValue('historyCommit');

  useEffect(() => {
    if (!loaded) return;
    if (scope && !scopeOptions.some(option => option.scope === scope)) {
      setScope('');
      return; // Wait for the new filter's derived options before reconciling.
    }
    if (actor && !actorOptions.some(option => option.type === actor)) {
      setActor(null);
      return;
    }
    if (needsActionSelected) return;
    if (commit && filteredCommits.some(item => item.commit_id === commit)) return;
    const next = filteredCommits.some(item => item.commit_id === headCommitId)
      ? headCommitId
      : filteredCommits[0]?.commit_id ?? null;
    if (next !== commit) setCommit(next);
  }, [loaded, scopeOptions, actorOptions, filteredCommits, headCommitId,
    needsActionSelected, scope, actor, commit, setScope, setActor, setCommit]);
}
