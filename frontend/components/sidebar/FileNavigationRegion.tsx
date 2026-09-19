'use client';

import type { ReactNode } from 'react';
import { useWorkspaceActions, useWorkspaceNavigation } from '@/features/workspace/responsive';
import { ResponsiveDrawer } from './ResponsiveDrawer';

/** Only this boundary subscribes to Files visibility. Its editor sibling does not. */
export function FileNavigationRegion({ children }: { children: ReactNode }) {
  const open = useWorkspaceNavigation(state => state.filesOpen);
  const { setFilesOpen } = useWorkspaceActions();
  return <ResponsiveDrawer id='workspace-files' label='Files' belowHeader open={open} onClose={() => setFilesOpen(false)}>{children}</ResponsiveDrawer>;
}
