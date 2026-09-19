'use client';

import type { NeedsActionSelection } from '@/features/history/components/NeedsActionSection';
import {
  getKind as getNeedsActionKind,
  type NeedsActionItem
} from '@/features/history/components/NeedsActionSection';
import type {
  NeedsActionRenderContext,
  ResolvedResult,
} from '@/lib/needsActionRegistry';

export function NeedsActionDetailPane({
  projectId,
  selection,
  item,
  onRemoved,
}: {
  projectId: string;
  selection: NeedsActionSelection;
  item: NeedsActionItem;
  onRemoved: (selection: NeedsActionSelection, result: ResolvedResult) => void;
}) {
  const def = getNeedsActionKind(selection.kind);
  if (!def) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100%', color: 'var(--po-text-disabled)', fontSize: 13,
      }}>
        Unknown item kind: {selection.kind}
      </div>
    );
  }
  const ctx: NeedsActionRenderContext = {
    projectId,
    isSelected: true,
    onSelect: () => {},
    onResolved: (result) => onRemoved(selection, result),
    onSnoozed: () => onRemoved(selection, { reason: 'dismissed' }),
  };
  return <>{def.renderDetail(item, ctx)}</>;
}
