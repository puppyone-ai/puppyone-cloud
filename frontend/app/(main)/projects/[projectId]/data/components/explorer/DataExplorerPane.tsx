'use client';

import { WorkspaceFileColumn } from '@/components/sidebar/WorkspaceFileColumn';
import { ExplorerSidebar } from './ExplorerSidebar';
import type { ExplorerSidebarProps } from './types';

type DataExplorerPaneProps = Omit<
  ExplorerSidebarProps,
  | 'currentPath'
  | 'activeNodeId'
  | 'activeSyncNodeId'
  | 'highlightNodeId'
  | 'highlightVariant'
  | 'style'
> & {
  folderBreadcrumbs: { id: string; name: string }[];
  activeNodeId?: string;
  activeSyncNodeId?: string | null;
  highlightNodeId?: string | null;
  hoverHighlightNodeId?: string | null;
};

export function DataExplorerPane({
  folderBreadcrumbs,
  activeNodeId,
  activeSyncNodeId,
  highlightNodeId,
  hoverHighlightNodeId,
  ...sidebarProps
}: DataExplorerPaneProps) {
  return (
    <WorkspaceFileColumn>
      <ExplorerSidebar
        {...sidebarProps}
        currentPath={folderBreadcrumbs.map((f) => ({ id: f.id, name: f.name }))}
        activeNodeId={activeNodeId}
        activeSyncNodeId={activeSyncNodeId}
        highlightNodeId={hoverHighlightNodeId || highlightNodeId}
        highlightVariant={hoverHighlightNodeId !== null ? 'access-point' : 'default'}
        style={{ flex: 1, width: '100%', background: 'transparent', minHeight: 0 }}
      />
    </WorkspaceFileColumn>
  );
}
