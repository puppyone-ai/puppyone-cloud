'use client';

import { useCallback, useState } from 'react';
import useSWR from 'swr';
import {
  getGitProjectHealth,
  rebuildGitProjectCache,
  type GitViewHealthPayload,
  type GitViewHealth,
} from '@/lib/gitHealthApi';
import { StatusDot } from '@/components/ui/StatusDot';

interface Props {
  projectId: string;
  /** When true, renders even when health is "healthy". Useful on
   * settings/admin surfaces where the operator wants to see the
   * current state at a glance. */
  alwaysShow?: boolean;
}

/**
 * Project-root Git view health banner.
 *
 * Subscribes to the JWT-authenticated Project control-plane health endpoint
 * (60s revalidation). The canonical ``/git/...`` data plane continues to
 * accept only a scope-bounded Git runtime credential.
 * and shows a colored chip when the view is degraded or corrupt. The
 * banner is silent on ``healthy`` and ``empty`` unless ``alwaysShow``
 * is set. Includes a "Rebuild cache" action that hits the project-
 * root rebuild-cache endpoint when the view reports
 * ``current_corrupt`` (the doc's recommended recovery path).
 */
export function ProjectGitHealthBadge({ projectId, alwaysShow = false }: Props) {
  const { data: health, isLoading, mutate } = useSWR<GitViewHealthPayload>(
    projectId ? ['git-project-health', projectId] : null,
    () => getGitProjectHealth(projectId),
    { refreshInterval: 60_000, revalidateOnFocus: true, dedupingInterval: 30_000 },
  );

  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildResult, setRebuildResult] = useState<'idle' | 'ok' | 'err'>('idle');

  const handleRebuild = useCallback(async () => {
    if (rebuilding) return;
    setRebuilding(true);
    setRebuildResult('idle');
    try {
      await rebuildGitProjectCache(projectId);
      setRebuildResult('ok');
      await mutate();
    } catch (err) {
      console.error('[ProjectGitHealth] rebuild failed:', err);
      setRebuildResult('err');
    } finally {
      setRebuilding(false);
    }
  }, [rebuilding, projectId, mutate]);

  if (!health && !isLoading) return null;
  const state: GitViewHealth | 'loading' = isLoading ? 'loading' : (health?.health ?? 'healthy');
  // ``state`` narrows to one of the two silent values inside the
  // guard; ``alwaysShow=true`` makes us render anyway. ``loading``
  // never collapses because the early return above already handled
  // the no-data case.
  const isSilent = !alwaysShow && (state === 'healthy' || state === 'empty');
  if (isSilent) return null;

  const tone = healthTone(state);
  const title = healthTitle(state);
  const detail = isLoading
    ? 'Checking project Git view…'
    : (health?.reason || healthDefaultDetail(state));

  // Show the rebuild button on current_corrupt (per spec: the
  // canonical recovery action). For history_degraded we leave the
  // user with diagnostic info — rebuilding doesn't typically fix
  // truncated legacy history.
  const showRebuildButton = state === 'current_corrupt' && health?.can_rebuild === true;

  return (
    <div
      style={{
        margin: '8px 0',
        padding: '10px 14px',
        borderRadius: 8,
        background: tone.background,
        border: `1px solid ${tone.border}`,
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
      }}
    >
      <StatusDot style={{ background: tone.color, marginTop: 5 }} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: tone.color, lineHeight: 1.5 }}>
          {title}
        </div>
        <div style={{ fontSize: 12, color: 'var(--po-text-muted)', lineHeight: 1.5 }}>
          {detail}
        </div>
      </div>
      {showRebuildButton && (
        <button
          type="button"
          onClick={handleRebuild}
          disabled={rebuilding}
          style={{
            padding: '4px 12px',
            borderRadius: 4,
            fontSize: 11,
            fontWeight: 600,
            border: `1px solid ${tone.border}`,
            background: tone.background,
            color: tone.color,
            cursor: rebuilding ? 'progress' : 'pointer',
            whiteSpace: 'nowrap',
            alignSelf: 'flex-start',
          }}
        >
          {rebuildResult === 'ok'
            ? 'Rebuilt ✓'
            : rebuildResult === 'err'
              ? 'Failed ✗'
              : rebuilding
                ? 'Rebuilding…'
                : 'Rebuild cache'}
        </button>
      )}
    </div>
  );
}


function healthTone(state: GitViewHealth | 'loading') {
  if (state === 'current_corrupt') {
    return {
      color: 'var(--po-danger)',
      border: 'color-mix(in srgb, var(--po-danger) 24%, transparent)',
      background: 'color-mix(in srgb, var(--po-danger) 6%, transparent)',
    };
  }
  if (state === 'history_degraded') {
    return {
      color: 'var(--po-warning)',
      border: 'color-mix(in srgb, var(--po-warning) 24%, transparent)',
      background: 'color-mix(in srgb, var(--po-warning) 8%, transparent)',
    };
  }
  if (state === 'empty') {
    return {
      color: 'var(--po-text-muted)',
      border: 'var(--po-border-subtle)',
      background: 'transparent',
    };
  }
  // healthy + loading
  return {
    color: 'var(--po-success)',
    border: 'color-mix(in srgb, var(--po-success) 18%, transparent)',
    background: 'color-mix(in srgb, var(--po-success) 6%, transparent)',
  };
}


function healthTitle(state: GitViewHealth | 'loading'): string {
  switch (state) {
    case 'current_corrupt':
      return 'Git view is corrupt';
    case 'history_degraded':
      return 'Git history is truncated';
    case 'empty':
      return 'No commits yet';
    case 'healthy':
      return 'Git view is healthy';
    default:
      return 'Checking Git view';
  }
}


function healthDefaultDetail(state: GitViewHealth | 'loading'): string {
  switch (state) {
    case 'current_corrupt':
      return 'Restore a healthy version or rebuild the cache to recover.';
    case 'history_degraded':
      return 'Clone and push are available from the safe truncated history.';
    case 'empty':
      return 'Push or write the first commit to initialize this view.';
    case 'healthy':
      return 'No action required — clone, fetch, and push are all available.';
    default:
      return '';
  }
}
