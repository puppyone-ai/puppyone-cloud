'use client';

import { useMemo, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { useWorkspaceLayout } from '@/features/workspace/layoutStore';
import { useWorkspaceRegions } from '@/features/workspace/regions';
import { useSidebarOverlay } from '@/features/workspace/useSidebarOverlay';
import { useSidebarSwipe } from '@/features/workspace/useSidebarSwipe';
import { usePaneMotion } from '@/features/workspace/usePaneMotion';

/** The same navigation tree remains in its region in every layout. */
export function ResponsiveDrawer({ open, onClose, label, id, children, belowHeader = false, collapsed = false }: {
  open: boolean; onClose: () => void; label: string; id: string; children: ReactNode; belowHeader?: boolean; collapsed?: boolean;
}) {
  const overlay = useWorkspaceLayout(layout => (belowHeader ? layout.files : layout.projects).presentation === 'overlay');
  const active = overlay && open;
  const motion = usePaneMotion(overlay ? open : !collapsed, String(overlay));
  const ref = useRef<HTMLDivElement>(null);
  const regions = useWorkspaceRegions();
  const covered = useMemo(() => belowHeader ? [regions.editor, regions.auxiliary, regions.inspector] : [regions.appContent], [belowHeader, regions]);
  const close = useSidebarOverlay({ active, modal: !belowHeader, panel: ref, covered, onClose, restoreFocus: !open });
  useSidebarSwipe(ref, active, 'left', close);
  // React 18 forwards inert as a string attribute.
  const inactiveAttributes: Record<string, string> = overlay && !open ? { inert: '' } : {};

  return <div className='workspace-drawer' data-overlay={overlay} data-open={open} data-motion={motion.phase} data-below-header={belowHeader} id={id} aria-hidden={overlay && !open ? true : undefined}
    {...inactiveAttributes}>
    <button type='button' className='workspace-drawer-backdrop' aria-label={`Close ${label.toLowerCase()}`} tabIndex={-1} onClick={close} />
    <div className='workspace-drawer-sheet' data-sidebar-swipe-surface ref={ref} role={active ? (belowHeader ? 'complementary' : 'dialog') : undefined} aria-modal={active && !belowHeader || undefined} aria-label={active ? label : undefined}>
      <div className='workspace-drawer-heading'><span>{label}</span><button type='button' data-drawer-close aria-label={`Close ${label.toLowerCase()} navigation`} onClick={close}><X size={20} /></button></div>
      {children}
    </div>
  </div>;
}
