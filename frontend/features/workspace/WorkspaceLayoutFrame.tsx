'use client';

import { useEffect, useLayoutEffect, useRef, type CSSProperties, type ReactNode } from 'react';
import { useWorkspaceLayout, useWorkspaceLayoutStore } from './layoutStore';
import type { WorkspaceSegments } from './paneLayout';

const useBeforePaint = typeof window === 'undefined' ? useEffect : useLayoutEffect;

/** Intersect native segments with the host, so safe areas and an embedded
 * workspace cannot accidentally budget pixels outside their content box. */
export function getWorkspaceSegments(rect: Pick<DOMRect, 'left' | 'top' | 'right' | 'bottom'>, segments?: readonly DOMRect[]): WorkspaceSegments | null {
  if (segments?.length !== 2) return null;
  const [first, second] = [...segments].sort((a, b) => a.top - b.top || a.left - b.left);
  if (first.right <= second.left) {
    const firstWidth = Math.min(rect.right, first.right) - Math.max(rect.left, first.left);
    const secondWidth = Math.min(rect.right, second.right) - Math.max(rect.left, second.left);
    return firstWidth > 0 && secondWidth > 0 ? { orientation: 'horizontal', first: firstWidth, second: secondWidth, gap: second.left - first.right } : null;
  }
  if (first.bottom <= second.top) {
    const firstHeight = Math.min(rect.bottom, first.bottom) - Math.max(rect.top, first.top);
    const secondHeight = Math.min(rect.bottom, second.bottom) - Math.max(rect.top, second.top);
    return firstHeight > 0 && secondHeight > 0 ? { orientation: 'vertical', first: firstHeight, second: secondHeight, gap: second.top - first.bottom } : null;
  }
  return null;
}

/** The only observer of horizontal workbench space. Children keep their
 * identities; only this frame and subscribed region chrome see pixel changes. */
export function WorkspaceLayoutFrame({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const store = useWorkspaceLayoutStore();
  const layout = useWorkspaceLayout(value => value);
  useBeforePaint(() => {
    const host = ref.current;
    if (!host) return;
    const update = () => {
      const rect = host.getBoundingClientRect();
      const style = getComputedStyle(host);
      const left = rect.left + (parseFloat(style.paddingLeft) || 0);
      const right = rect.right - (parseFloat(style.paddingRight) || 0);
      const top = rect.top + (parseFloat(style.paddingTop) || 0);
      const bottom = rect.bottom - (parseFloat(style.paddingBottom) || 0);
      const viewport = (window as Window & { viewport?: { segments?: readonly DOMRect[] } }).viewport;
      store.getState().setEnvironment({ availableWidth: Math.max(0, right - left), segments: getWorkspaceSegments({ left, right, top, bottom }, viewport?.segments) });
    };
    update();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
    observer?.observe(host);
    // Also observes segment topology changes at unchanged host dimensions.
    window.addEventListener('resize', update);
    return () => { observer?.disconnect(); window.removeEventListener('resize', update); };
  }, [store]);
  return <div ref={ref} className='workspace-root'
    data-projects-layout={layout.projects.presentation} data-files-layout={layout.files.presentation}
    data-auxiliary-layout={layout.auxiliary.presentation} data-shared-header={layout.sharedHeader}
    data-inspector-layout={layout.inspector.presentation}
    data-workspace-segments={layout.segments?.orientation ?? 'none'}
    style={{
      display: 'flex', overflow: 'hidden', backgroundColor: 'var(--po-canvas)',
      '--workspace-projects-width': `${layout.projects.width}px`,
      '--workspace-files-width': `${layout.files.width}px`,
      '--workspace-auxiliary-width': `${layout.auxiliary.width}px`,
      '--workspace-inspector-width': `${layout.inspector.width}px`,
      '--workspace-segment-first': `${layout.segments?.first ?? 0}px`,
      '--workspace-segment-second': `${layout.segments?.second ?? 0}px`,
      '--workspace-segment-gap': `${layout.segments?.gap ?? 0}px`,
    } as CSSProperties}>{children}</div>;
}
