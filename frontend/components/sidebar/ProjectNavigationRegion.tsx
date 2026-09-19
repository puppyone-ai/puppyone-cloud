'use client';

import type { ComponentProps } from 'react';
import { useWorkspaceActions, useWorkspaceNavigation } from '@/features/workspace/responsive';
import { useWorkspaceLayout, useWorkspacePane } from '@/features/workspace/layoutStore';
import { ResponsiveDrawer } from './ResponsiveDrawer';
import { WorkspaceProjectRail } from './WorkspaceProjectRail';

export function ProjectNavigationRegion(props: ComponentProps<typeof WorkspaceProjectRail>) {
  useWorkspacePane('projects', { present: true, preferredWidth: props.sidebarWidth ?? 220, minWidth: 160, maxWidth: 360, collapsed: props.isCollapsed ?? false, collapsedWidth: 56 });
  const pane = useWorkspaceLayout(layout => layout.projects);
  const open = useWorkspaceNavigation(state => state.projectsOpen);
  const { setProjectsOpen } = useWorkspaceActions();
  return <ResponsiveDrawer id='workspace-projects' label='Projects' open={open} collapsed={props.isCollapsed} onClose={() => setProjectsOpen(false)}>
    <WorkspaceProjectRail {...props} sidebarWidth={pane.width} maxResizeWidth={pane.resizeMax} resizable={pane.presentation === 'docked'} isCollapsed={pane.presentation === 'docked' && props.isCollapsed} />
  </ResponsiveDrawer>;
}
