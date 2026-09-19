'use client';

import { useMemo, useRef, useState, type ReactNode } from 'react';
import { ResizablePanel } from '@/components/RightAuxiliaryPanel/ResizablePanel';
import { useFileDrawer, useWorkspaceNavigation } from '@/features/workspace/responsive';
import { useWorkspaceLayout, useWorkspacePane } from '@/features/workspace/layoutStore';
import { useWorkspaceRegions } from '@/features/workspace/regions';
import { useSidebarOverlay } from '@/features/workspace/useSidebarOverlay';
import { useSidebarSwipe } from '@/features/workspace/useSidebarSwipe';
import { usePaneMotion } from '@/features/workspace/usePaneMotion';

/** Layout/interaction subscriber; children are owned by the project session. */
export function WorkspaceAuxiliaryRegion({ visible, label, trigger, onClose, children }: {
  visible: boolean; label: string; trigger: 'chat' | 'access'; onClose: () => void; children: ReactNode;
}) {
  const [preferredWidth, setPreferredWidth] = useState(450);
  const motion = usePaneMotion(visible);
  useWorkspacePane('auxiliary', { present: true, open: motion.present, preferredWidth, minWidth: 300, maxWidth: 800 });
  const pane = useWorkspaceLayout(layout => layout.auxiliary);
  const sharedHeader = useWorkspaceLayout(layout => layout.sharedHeader);
  const segmented = useWorkspaceLayout(layout => Boolean(layout.segments));
  const projectsOverlay = useWorkspaceLayout(layout => layout.projects.presentation === 'overlay');
  const overlayOwner = useWorkspaceLayout(layout => layout.overlayOwner);
  const retainedWidth = useRef(pane.width);
  if (visible) retainedWidth.current = pane.width;
  const fileDrawer = useFileDrawer();
  const filesOpen = useWorkspaceNavigation(state => state.filesOpen);
  const projectsOpen = useWorkspaceNavigation(state => state.projectsOpen);
  const regions = useWorkspaceRegions();
  const active = sharedHeader && visible && overlayOwner !== 'inspector' && !(projectsOverlay && projectsOpen) && !(fileDrawer && filesOpen);
  const covered = useMemo(() => pane.presentation === 'overlay' ? [regions.projectBody] : [], [pane.presentation, regions]);
  const close = useSidebarOverlay({ active, panel: regions.auxiliary, covered, onClose, restoreFocus: !visible,
    returnFocus: () => regions.header.current?.querySelector<HTMLButtonElement>(`[data-workspace-${trigger}-trigger]`) ?? null,
  });
  useSidebarSwipe(regions.auxiliary, active, 'right', close);
  return <>
    <button type='button' className='workspace-auxiliary-backdrop' data-open={visible} aria-label='Close sidebar' tabIndex={-1} onClick={close} />
    <ResizablePanel className='workspace-auxiliary' panelRef={regions.auxiliary} ariaLabel={label}
      width={pane.width} contentWidth={visible && motion.phase === 'idle' ? undefined : Math.max(0, retainedWidth.current - 1)} onWidthChange={setPreferredWidth} maxWidth={pane.resizeMax}
      motion={motion.phase} onMotionEnd={motion.finish}
      resizable={pane.presentation === 'docked' && !segmented && motion.phase === 'idle'}
      isVisible={visible} layout='region' borderLeftColor='var(--po-divider)' background='var(--po-canvas)' zIndex={80}>
      <div className='workspace-sidebar-swipe-edge' data-sidebar-swipe-surface aria-hidden='true' />
      {children}
    </ResizablePanel>
  </>;
}
