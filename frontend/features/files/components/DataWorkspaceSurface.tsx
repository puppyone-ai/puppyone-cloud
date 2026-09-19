'use client';

import { PageLoading } from '@/components/loading';
import { ReadErrorState } from '@/components/loading/ReadErrorState';
import { FileNavigationRegion } from '@/components/sidebar/FileNavigationRegion';
import { BulkDeleteDialog } from '@/features/files/components/BulkDeleteDialog';
import { DataNoFileSelectedState } from '@/features/files/components/DataNoFileSelectedState';
import { DataPageDialogs } from '@/features/files/components/DataPageDialogs';
import { DataPageOverlays } from '@/features/files/components/DataPageOverlays';
import { EditorArea } from '@/features/files/components/EditorArea';
import { SelectionActionBar } from '@/features/files/components/SelectionActionBar';
import { DataExplorerPane } from '@/features/files/components/explorer';
import { DataPageRightPanel } from '@/features/files/components/right-panel';
import { GridView } from '@/features/files/components/views';
import { EmptyWorkspaceState } from '@/features/projects/components/EmptyWorkspaceState';
import { useWorkspaceRegions } from '@/features/workspace/regions';
import { useWorkspaceActions } from '@/features/workspace/responsive';
import type { ComponentProps, CSSProperties, ReactNode } from 'react';

type DataWorkspaceSurfaceProps = {
  dialogsProps: ComponentProps<typeof DataPageDialogs>;
  overlaysProps: ComponentProps<typeof DataPageOverlays>;
  bulkDeleteProps: ComponentProps<typeof BulkDeleteDialog>;
  selectionProps: ComponentProps<typeof SelectionActionBar>;
  explorer: {
    hidden: boolean;
    props: ComponentProps<typeof DataExplorerPane>;
  };
  content: {
    isResolvingPath: boolean;
    fileReadError?: unknown;
    retryFileRead: () => void;
    isEditorView: boolean;
    isProjectIdentityLoading: boolean;
    editorAreaProps: ComponentProps<typeof EditorArea> | null;
    isFolderView: boolean;
    isRootEmptyDecisionLoading: boolean;
    isLoading: boolean;
    readError?: unknown;
    onRetryRead: () => void;
    showEmptyWorkspace: boolean;
    suppressExplorerSidebar: boolean;
    emptyWorkspaceProps: ComponentProps<typeof EmptyWorkspaceState>;
    noFileSelectedProps: ComponentProps<typeof DataNoFileSelectedState>;
    gridViewProps: ComponentProps<typeof GridView>;
  };
  rightPanelProps: ComponentProps<typeof DataPageRightPanel>;
  accessModalSlot?: ReactNode;
};

export function DataWorkspaceSurface({
  dialogsProps,
  overlaysProps,
  bulkDeleteProps,
  selectionProps,
  explorer,
  content,
  rightPanelProps,
  accessModalSlot,
}: DataWorkspaceSurfaceProps) {
  const { setFilesOpen } = useWorkspaceActions();
  const regions = useWorkspaceRegions();
  return (
    <>
      <DataPageDialogs {...dialogsProps} />
      <DataPageOverlays {...overlaysProps} />
      <BulkDeleteDialog {...bulkDeleteProps} />
      <SelectionActionBar {...selectionProps} />

      <div
        style={{
          flex: 1,
          display: 'flex',
          minHeight: 0,
          minWidth: 0,
          position: 'relative',
          overflow: 'hidden',
        } as CSSProperties}
      >
        <div
          className='workspace-data-content'
          style={{
            flex: 1,
            display: 'flex',
            minHeight: 0,
            position: 'relative',
            zIndex: 70,
          }}
        >
          {!explorer.hidden && <FileNavigationRegion>
            <DataExplorerPane {...explorer.props} onNavigate={item => { const accepted = explorer.props.onNavigate(item); if (accepted !== false) setFilesOpen(false); return accepted; }} />
          </FileNavigationRegion>}

          <div ref={regions.editor} className='workspace-editor-viewport' style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>
            <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              {content.fileReadError ? <ReadErrorState label='Could not load this file or folder.' onRetry={content.retryFileRead} /> : null}
              {content.isResolvingPath && !content.fileReadError && (
                <div style={{ flex: 1, background: 'var(--po-canvas)' }}>
                  <PageLoading variant="fill" />
                </div>
              )}

              {content.isEditorView &&
                !content.fileReadError &&
                !content.isResolvingPath &&
                !content.editorAreaProps &&
                content.isProjectIdentityLoading && (
                  <div style={{ flex: 1, background: 'var(--po-canvas)' }}>
                    <PageLoading variant="fill" />
                  </div>
                )}

              {content.isEditorView &&
                !content.fileReadError &&
                !content.isResolvingPath &&
                content.editorAreaProps && (
                  <div style={{ flex: 1, position: 'relative', display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0 }}>
                    <EditorArea {...content.editorAreaProps} />
                  </div>
                )}

              {content.isFolderView && !content.isResolvingPath && !content.fileReadError && (
                <div className='workspace-folder-content' style={{ flex: 1, overflow: 'auto', padding: content.suppressExplorerSidebar ? 0 : 24, display: 'flex', flexDirection: 'column' }}>
                  {content.readError ? (
                    <ReadErrorState label='Could not load this folder.' onRetry={content.onRetryRead} />
                  ) : content.isRootEmptyDecisionLoading ? (
                    <div style={{ flex: 1, minHeight: 200, background: 'var(--po-canvas)' }}>
                      <PageLoading variant="fill" />
                    </div>
                  ) : content.isLoading ? (
                    <div style={{ height: '100%', minHeight: 200 }}>
                      <PageLoading variant="fill" />
                    </div>
                  ) : content.showEmptyWorkspace ? (
                    <EmptyWorkspaceState {...content.emptyWorkspaceProps} />
                  ) : (
                    <DataNoFileSelectedState {...content.noFileSelectedProps} />
                  )}
                </div>
              )}
            </div>
          </div>

        </div>

        <DataPageRightPanel {...rightPanelProps} />
        {accessModalSlot}
      </div>
    </>
  );
}
