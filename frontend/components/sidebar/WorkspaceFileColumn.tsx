'use client';

import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react';
import { useWorkspaceLayout, useWorkspacePane } from '@/features/workspace/layoutStore';
import { ResizableSidebarColumn } from './ResizableSidebarColumn';

const STORAGE_KEY = 'sidebar-width:explorer-sidebar:data';
const MIN_WIDTH = 220;
const MAX_WIDTH = 480;
const useBeforePaint = typeof window === 'undefined' ? useEffect : useLayoutEffect;

/** The Files region owns its preference; the reusable size frame receives
 * resolved geometry. Passive compression is never persisted as a preference. */
export function WorkspaceFileColumn({ children }: { children: ReactNode }) {
  const [preferredWidth, setPreferredWidth] = useState(MIN_WIDTH);
  useBeforePaint(() => {
    try {
      const saved = Number(window.localStorage.getItem(STORAGE_KEY));
      if (Number.isFinite(saved) && saved > 0) setPreferredWidth(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, saved)));
    } catch { /* Storage is optional in private/sandboxed contexts. */ }
  }, []);
  useWorkspacePane('files', { present: true, preferredWidth, minWidth: MIN_WIDTH, maxWidth: MAX_WIDTH });
  const pane = useWorkspaceLayout(layout => layout.files);
  const commitWidth = (width: number) => {
    setPreferredWidth(width);
    try { window.localStorage.setItem(STORAGE_KEY, String(Math.round(width))); } catch { /* Optional persistence. */ }
  };
  return <ResizableSidebarColumn storageKey='explorer-sidebar:data'
    width={pane.width || preferredWidth} onWidthChange={commitWidth}
    minWidth={MIN_WIDTH} maxWidth={pane.resizeMax} resizable={pane.presentation === 'docked'}
    style={{ borderRight: '1px solid var(--po-divider)', background: 'var(--po-canvas)', transition: 'none' }}>
    {children}
  </ResizableSidebarColumn>;
}
