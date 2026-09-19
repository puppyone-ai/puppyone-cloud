'use client';

import { memo, useCallback, useEffect, useRef, useState } from 'react';
import type { DragEvent } from 'react';
import { Plus } from 'lucide-react';
import { useExplorerRootNodes } from '@/lib/hooks/useData';
import { useNodeDrop } from '@/lib/hooks/useNodeDrop';
import {
  resolveDataTransferSnapshot,
  snapshotDataTransfer,
} from '@/lib/dropFiles';
import type { ContentType } from '../views/GridView';
import type { FileImportTarget } from '../../hooks/useFileImport';
import { ensureExpandedBatch, usePendingActiveId } from './explorerState';
import {
  EXPLORER_TREE_ROW_HEIGHT,
  EXPLORER_TREE_ROW_MARGIN_X,
  EXPLORER_TREE_ROW_MARGIN_Y,
  ExplorerTreeMetaRow,
  ExplorerTreeRow,
} from './ExplorerTreeRow';
import type { ExplorerSidebarProps, MillerColumnItem } from './types';
import { DirectoryLoadingState } from '@/components/loading/DirectoryLoadingState';
import { ReadErrorState } from '@/components/loading/ReadErrorState';
import { SIDEBAR_META_TYPOGRAPHY } from '@/lib/uiTypography';

const FILE_DROP_ROOT_SCOPE_BG = 'var(--po-hover)';
const FILE_DROP_TARGET_BORDER = 'var(--po-border-strong)';
const FILE_DROP_SCOPE_BORDER = 'var(--po-border)';
const ROOT_DROP_TARGET: FileImportTarget = { path: null, name: 'Root' };

function hasExternalFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer.types).includes('Files');
}

export const ExplorerSidebar = memo(function ExplorerSidebar({
  projectId,
  currentPath,
  activeNodeId,
  onNavigate,
  onCreate,
  onCreateSync,
  onOpenAccess,
  endpointByNodeId,
  onRename,
  onDelete,
  onDownload,
  onFilesDrop,
  onMoveNode,
  activeSyncNodeId,
  highlightNodeId,
  highlightVariant = 'default',
  createMenuOpenForId,
  createMenuOpenAction,
  className,
  style,
}: ExplorerSidebarProps) {
  const {
    rootNodes,
    isLoading: loading,
    error: rootLoadError,
    refresh,
  } = useExplorerRootNodes(projectId);
  const sidebarFileDragCounterRef = useRef(0);
  const [isExternalFileDraggingInSidebar, setIsExternalFileDraggingInSidebar] =
    useState(false);
  const [activeFileDropTarget, setActiveFileDropTarget] =
    useState<FileImportTarget | null>(null);
  const { isDropTarget: isRootDropTarget, dropHandlers: rootDropHandlers } =
    useNodeDrop({
      targetFolderId: null,
      onMoveNode,
    });

  const currentPathIds = currentPath.map(p => p.id);
  const currentPathKey = currentPathIds.join('\0');

  useEffect(() => {
    if (currentPathIds.length > 0) {
      ensureExpandedBatch(projectId, currentPathIds);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, currentPathKey]);

  const rootItems: MillerColumnItem[] = rootNodes.map(node => ({
    id: node.id,
    name: node.name,
    type: node.type as ContentType,
    is_synced: node.is_synced,
    sync_source: node.sync_source,
    last_synced_at: node.last_synced_at,
    integrity_status: node.integrity_status,
  }));

  const pendingId = usePendingActiveId();
  const activeId = pendingId || activeNodeId || null;
  const isRootFileDropTarget =
    isExternalFileDraggingInSidebar && activeFileDropTarget?.path === null;
  const isRootCreateMenuOpen = createMenuOpenForId === '__root__';

  const handleSidebarDragEnterCapture = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      if (!hasExternalFiles(event)) return;
      sidebarFileDragCounterRef.current += 1;
      setIsExternalFileDraggingInSidebar(true);
      setActiveFileDropTarget(current => current ?? ROOT_DROP_TARGET);
    },
    []
  );

  const handleSidebarDragLeaveCapture = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      if (!hasExternalFiles(event)) return;
      sidebarFileDragCounterRef.current -= 1;
      if (sidebarFileDragCounterRef.current <= 0) {
        sidebarFileDragCounterRef.current = 0;
        setIsExternalFileDraggingInSidebar(false);
        setActiveFileDropTarget(null);
      }
    },
    []
  );

  const handleSidebarDropCapture = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      if (!hasExternalFiles(event)) return;
      sidebarFileDragCounterRef.current = 0;
      setIsExternalFileDraggingInSidebar(false);
      setActiveFileDropTarget(null);
    },
    []
  );

  const activateRootDropTarget = useCallback(
    (event: DragEvent<HTMLElement>) => {
      if (!hasExternalFiles(event)) return false;
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = 'copy';
      setIsExternalFileDraggingInSidebar(true);
      setActiveFileDropTarget(ROOT_DROP_TARGET);
      return true;
    },
    []
  );

  const handleRootFileDrop = useCallback(
    (event: DragEvent<HTMLElement>) => {
      if (!hasExternalFiles(event)) return false;
      event.preventDefault();
      event.stopPropagation();
      sidebarFileDragCounterRef.current = 0;
      setIsExternalFileDraggingInSidebar(false);
      setActiveFileDropTarget(null);

      // Snapshot the DataTransfer SYNCHRONOUSLY — see lib/dropFiles.ts.
      // Reading items after this handler returns yields null entries
      // in Safari/Firefox, which would silently drop folder contents.
      const snapshot = snapshotDataTransfer(event.nativeEvent);
      void resolveDataTransferSnapshot(snapshot).then(files => {
        if (files.length > 0) onFilesDrop?.(files, ROOT_DROP_TARGET);
      });
      return true;
    },
    [onFilesDrop]
  );

  return (
    <div
      data-explorer-sidebar-root='true'
      className={className}
      onDragEnterCapture={handleSidebarDragEnterCapture}
      onDragLeaveCapture={handleSidebarDragLeaveCapture}
      onDropCapture={handleSidebarDropCapture}
      style={{
        ...style,
        display: 'flex',
        flexDirection: 'column',
        boxSizing: 'border-box',
        minWidth: 0,
      }}
    >
      <div
        data-explorer-scroll='true'
        onDragEnter={event => {
          if (!activateRootDropTarget(event))
            rootDropHandlers.onDragEnter(event);
        }}
        onDragOver={event => {
          if (!activateRootDropTarget(event))
            rootDropHandlers.onDragOver(event);
        }}
        onDragLeave={rootDropHandlers.onDragLeave}
        onDrop={event => {
          if (!handleRootFileDrop(event)) rootDropHandlers.onDrop(event);
        }}
        style={{
          flex: 1,
          minHeight: 0,
          overflow: 'auto',
          overflowX: 'hidden',
          scrollbarGutter: 'auto',
          position: 'relative',
          background:
            isRootFileDropTarget || isRootDropTarget
              ? FILE_DROP_ROOT_SCOPE_BG
              : 'transparent',
          boxShadow:
            isRootFileDropTarget || isRootDropTarget
              ? `inset 0 0 0 1px ${isRootFileDropTarget ? FILE_DROP_SCOPE_BORDER : FILE_DROP_TARGET_BORDER}`
              : 'none',
          transition: 'background 0.12s ease, box-shadow 0.12s ease',
        }}
      >
        <div
          style={{
            width: '100%',
            padding: '6px 0',
            position: 'relative',
            boxSizing: 'border-box',
          }}
        >
          {rootLoadError ? <ReadErrorState label='Could not load files.' onRetry={() => { void refresh().catch(() => {}); }} /> : null}
          {loading && rootItems.length === 0 ? (
            <DirectoryLoadingState />
          ) : rootItems.length === 0 && !rootLoadError ? (
            <ExplorerTreeMetaRow depth={0}>
              <span
                style={{
                  ...SIDEBAR_META_TYPOGRAPHY,
                  color: 'var(--po-text-disabled)',
                }}
              >
                Empty folder
              </span>
            </ExplorerTreeMetaRow>
          ) : (
            rootItems.map(item => (
              <ExplorerTreeRow
                key={`${projectId}:${item.id}`}
                item={item}
                depth={0}
                projectId={projectId}
                activeId={activeId}
                onNavigate={onNavigate}
                onCreate={onCreate}
                onCreateSync={onCreateSync}
                onOpenAccess={onOpenAccess}
                endpointByNodeId={endpointByNodeId}
                onRename={onRename}
                onDelete={onDelete}
                onDownload={onDownload}
                onFilesDrop={onFilesDrop}
                activeFileDropTargetPath={activeFileDropTarget?.path}
                onFileDragTarget={setActiveFileDropTarget}
                onMoveNode={onMoveNode}
                activeSyncNodeId={activeSyncNodeId}
                highlightNodeId={highlightNodeId}
                highlightVariant={highlightVariant}
                createMenuOpenForId={createMenuOpenForId}
                createMenuOpenAction={createMenuOpenAction}
              />
            ))
          )}

          {onCreate && (
            <button
              type='button'
              onClick={event => onCreate(event, null)}
              className='group/row'
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                width: `calc(100% - ${EXPLORER_TREE_ROW_MARGIN_X * 2}px)`,
                height: EXPLORER_TREE_ROW_HEIGHT,
                margin: `${EXPLORER_TREE_ROW_MARGIN_Y}px ${EXPLORER_TREE_ROW_MARGIN_X}px`,
                padding: '0 8px',
                boxSizing: 'border-box',
                border: 0,
                borderRadius: 6,
                background: isRootCreateMenuOpen
                  ? 'var(--po-selected)'
                  : 'transparent',
                color: isRootCreateMenuOpen
                  ? 'var(--po-text)'
                  : 'var(--po-text-muted)',
                fontFamily: 'var(--po-font-sans)',
                fontSize: 14,
                fontWeight: 400,
                lineHeight: 1.25,
                textAlign: 'left',
                cursor: 'pointer',
                transition: 'background 0.1s, color 0.1s',
              }}
              onMouseEnter={event => {
                if (!isRootCreateMenuOpen)
                  event.currentTarget.style.background = 'var(--po-hover)';
                event.currentTarget.style.color = 'var(--po-text)';
              }}
              onMouseLeave={event => {
                if (!isRootCreateMenuOpen)
                  event.currentTarget.style.background = 'transparent';
                if (!isRootCreateMenuOpen)
                  event.currentTarget.style.color = 'var(--po-text-muted)';
              }}
            >
              <span
                style={{
                  display: 'flex',
                  width: 18,
                  height: 18,
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Plus size={14} strokeWidth={2.2} />
              </span>
              <span>Create new</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
});
