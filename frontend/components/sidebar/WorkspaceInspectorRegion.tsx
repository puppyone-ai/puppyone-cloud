'use client';

import { useMemo, useRef, useState, type ReactNode } from 'react';
import { ResizablePanel } from '@/components/RightAuxiliaryPanel/ResizablePanel';
import { useWorkspaceLayout, useWorkspacePane } from '@/features/workspace/layoutStore';
import { useWorkspaceNavigation } from '@/features/workspace/responsive';
import { useWorkspaceRegions } from '@/features/workspace/regions';
import { useSidebarOverlay } from '@/features/workspace/useSidebarOverlay';
import { usePaneMotion } from '@/features/workspace/usePaneMotion';

/** File-specific panels share the workspace budget, while their content and
 * lifecycle remain owned by the file. The frame is the only pixel subscriber. */
export function WorkspaceInspectorRegion({ visible, onClose, background, children }: {
  visible: boolean;
  onClose: () => void; background: string; children: ReactNode;
}) {
  const [preferredWidth, setPreferredWidth] = useState(450);
  const motion = usePaneMotion(visible);
  useWorkspacePane('inspector', { present: true, open: motion.present, preferredWidth, minWidth: 300, maxWidth: 800 });
  const pane = useWorkspaceLayout(layout => layout.inspector);
  const filesOverlay = useWorkspaceLayout(layout => layout.files.presentation === 'overlay');
  const projectsOverlay = useWorkspaceLayout(layout => layout.projects.presentation === 'overlay');
  const overlayOwner = useWorkspaceLayout(layout => layout.overlayOwner);
  const filesOpen = useWorkspaceNavigation(state => state.filesOpen);
  const projectsOpen = useWorkspaceNavigation(state => state.projectsOpen);
  const regions = useWorkspaceRegions();
  const covered = useMemo(() => [regions.editor], [regions]);
  useSidebarOverlay({ active: visible && overlayOwner === 'inspector' && !(filesOverlay && filesOpen) && !(projectsOverlay && projectsOpen),
    panel: regions.inspector, covered, onClose, restoreFocus: !visible });
  const retainedWidth = useRef(pane.width);
  if (visible) retainedWidth.current = pane.width;
  const retainedContent = useRef({ children, background });
  if (visible) retainedContent.current = { children, background };
  const content = motion.phase === 'closing' ? retainedContent.current : { children, background };
  return <ResizablePanel className='workspace-inspector' panelRef={regions.inspector} ariaLabel='File details'
    isVisible={visible} layout='inspector-region' zIndex={80} background={content.background}
    width={pane.width} maxWidth={pane.resizeMax} resizable={pane.presentation === 'docked' && motion.phase === 'idle'}
    motion={motion.phase} onMotionEnd={motion.finish}
    contentWidth={visible && motion.phase === 'idle' ? undefined : Math.max(0, retainedWidth.current - 1)}
    onWidthChange={setPreferredWidth}>
    {content.children}
  </ResizablePanel>;
}
