import type { CSSProperties } from 'react';
import { SIDEBAR_ROW_TYPOGRAPHY } from '@/lib/uiTypography';

// Row geometry is intentionally shared with the other secondary
// sidebars. Lines are a visual overlay only; do not solve line gaps by
// changing the row height, font size, or padding.
export const EXPLORER_TREE_ROW_HEIGHT = 32;
export const EXPLORER_TREE_ROW_GAP = 2;
export const EXPLORER_TREE_INDENT = 16;
export const EXPLORER_TREE_ROW_MARGIN_X = 6;
export const EXPLORER_TREE_CONTENT_INSET = 8;
export const EXPLORER_TREE_ROW_MARGIN_Y = EXPLORER_TREE_ROW_GAP / 2;
const EXPLORER_TREE_LINE_OVERDRAW = 2;

export const explorerRowStyle: CSSProperties = {
  display: 'flex', alignItems: 'center',
  margin: `${EXPLORER_TREE_ROW_MARGIN_Y}px ${EXPLORER_TREE_ROW_MARGIN_X}px`,
  height: EXPLORER_TREE_ROW_HEIGHT, boxSizing: 'border-box', borderRadius: 6,
  ...SIDEBAR_ROW_TYPOGRAPHY, userSelect: 'none',
  transition: 'background 0.14s, color 0.14s, opacity 0.18s', position: 'relative',
};
export function explorerLabelStyle(depth: number, right = 6): CSSProperties {
  return { flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 6,
    height: '100%', boxSizing: 'border-box', paddingLeft: EXPLORER_TREE_CONTENT_INSET + depth * EXPLORER_TREE_INDENT,
    paddingRight: right, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };
}
export const explorerIconStyle: CSSProperties = {
  display: 'flex', alignItems: 'center', flexShrink: 0, width: 18, height: 18,
  justifyContent: 'center', position: 'relative',
};

export function TreeIndentGuide({ depth }: { depth: number }) {
  if (depth <= 0) return null;

  return (
    <div
      aria-hidden
      style={{
        position: 'absolute',
        left: depth * EXPLORER_TREE_INDENT,
        top: -EXPLORER_TREE_LINE_OVERDRAW,
        bottom: -EXPLORER_TREE_LINE_OVERDRAW,
        width: 1,
        background: 'var(--po-tree-guide)',
        pointerEvents: 'none',
        zIndex: 0,
      }}
    />
  );
}
