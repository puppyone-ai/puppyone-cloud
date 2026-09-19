'use client';

import type { ComponentProps, CSSProperties, ReactNode } from 'react';
import { FileNavigationRegion } from '@/components/sidebar/FileNavigationRegion';
import { useWorkspaceRegions } from '@/features/workspace/regions';
import { useWorkspaceActions } from '@/features/workspace/responsive';
import { PageLoading } from '@/components/loading';
import { ReadErrorState } from '@/components/loading/ReadErrorState';
import { EmptyWorkspaceState } from '../../../components/EmptyWorkspaceState';
import { BulkDeleteDialog } from './BulkDeleteDialog';
import { DataPageDialogs } from './DataPageDialogs';
import { DataPageOverlays } from './DataPageOverlays';
import { EditorArea } from './EditorArea';
import { DataNoFileSelectedState } from './DataNoFileSelectedState';
import { SelectionActionBar } from './SelectionActionBar';
import { DataExplorerPane } from './explorer';
import { DataPageRightPanel } from './right-panel';
import { GridView } from './views';

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
            <DataExplorerPane {...explorer.props} onNavigate={item => { explorer.props.onNavigate(item); setFilesOpen(false); }} />
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
