'use client';

import { useCallback, useMemo } from 'react';
import { Dots } from '@/components/loading';
import { ActivityIconButton } from './ActivityIconButton';
import {
  ACTIVITY_BG,
  ACTIVITY_BORDER,
  ACTIVITY_SHADOW,
  ACTIVITY_WIDTH,
  activityHeaderStyle,
  activityTitleStyle,
} from './activityStyles';
import { cancelImportJob } from '@/lib/importApi';
import type { ActivityItem } from '@/lib/activityApi';

type ImportJobsWidgetProps = {
  activeItems: readonly ActivityItem[];
  onRefresh: () => Promise<unknown>;
  inline?: boolean;
};

/**
 * Transient widget for in-progress one-shot imports.
 *
 * Consumes the unified activity feed filtered to `import`, so imports render
 * from the same `context_activity_items` aggregation view as uploads and syncs
 * (sibling of SyncJobsWidget) instead of a separate import-jobs pipeline.
 * Import items stay cancellable — an import activity item's `id` is its
 * import_job id, so `cancelImportJob(item.id)` targets the right row.
 */
export function ImportJobsWidget({
  activeItems,
  onRefresh,
  inline = false,
}: ImportJobsWidgetProps) {
  const runs = useMemo(() => activeItems.slice(0, 3), [activeItems]);

  const handleCancel = useCallback(async (item: ActivityItem) => {
    await cancelImportJob(item.id);
    await onRefresh();
  }, [onRefresh]);

  if (activeItems.length === 0) return null;

  const primary = runs[0];
  const title = activeItems.length === 1 ? 'Importing' : `${activeItems.length} imports`;

  const containerStyle: React.CSSProperties = inline
    ? { position: 'relative', fontFamily: 'var(--po-font-sans)' }
    : {
        position: 'fixed',
        bottom: 20,
        right: 20,
        zIndex: 9999,
        fontFamily: 'var(--po-font-sans)',
      };

  return (
    <div style={containerStyle}>
      <div
        style={{
          width: ACTIVITY_WIDTH,
          background: ACTIVITY_BG,
          border: ACTIVITY_BORDER,
          borderRadius: 12,
          boxShadow: ACTIVITY_SHADOW,
          backdropFilter: 'blur(28px) saturate(160%)',
          WebkitBackdropFilter: 'blur(28px) saturate(160%)',
          overflow: 'hidden',
          color: 'var(--po-text)',
        }}
      >
        <div style={activityHeaderStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <Dots size="xs" tone="info" ariaLabel="Importing" />
            <span style={activityTitleStyle}>{title}</span>
          </div>
          {primary ? (
            <ActivityIconButton
              kind="close"
              title="Cancel import"
              onClick={() => handleCancel(primary)}
            />
          ) : null}
        </div>

        <div style={{ padding: '0 12px 12px' }}>
          {runs.map(item => (
            <div key={item.id} style={{ paddingTop: 9 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <div
                  style={{
                    minWidth: 0,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    fontSize: 12,
                    color: 'var(--po-text)',
                  }}
                  title={item.label || undefined}
                >
                  {item.label || 'Import'}
                </div>
                <div
                  style={{
                    flexShrink: 0,
                    fontSize: 11,
                    color: 'var(--po-text-subtle)',
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {Math.max(0, Math.min(100, item.progress ?? 0))}%
                </div>
              </div>
              <div
                style={{
                  marginTop: 6,
                  height: 3,
                  overflow: 'hidden',
                  borderRadius: 999,
                  background: 'var(--po-border-subtle)',
                }}
              >
                <div
                  style={{
                    width: `${Math.max(4, Math.min(100, item.progress || 8))}%`,
                    height: '100%',
                    borderRadius: 999,
                    background: 'var(--po-accent)',
                    transition: 'width 0.18s ease-out',
                  }}
                />
              </div>
              <div
                style={{
                  marginTop: 5,
                  fontSize: 11,
                  lineHeight: '15px',
                  color: 'var(--po-text-subtle)',
                }}
              >
                {item.message || item.phase || 'Importing'}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
