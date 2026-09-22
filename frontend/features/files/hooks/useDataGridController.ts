'use client';

import { type MillerColumnItem } from '@/features/files/components/explorer';
import type { ContentType } from '@/features/files/components/views';
import { useGridSelection } from '@/features/files/hooks/useGridSelection';
import type { NodeInfo } from '@/lib/contentTreeApi';
import { useCallback, useEffect, useMemo, useState } from 'react';

export function useDataGridController({
  contentNodes,
  currentFolderId,
  navigateTo,
  handleBulkDelete,
  refresh,
}: {
  contentNodes: NodeInfo[];
  currentFolderId: string | null;
  navigateTo: (nextPath: string[], typeHint?: string) => boolean;
  handleBulkDelete: (paths: string[]) => Promise<void>;
  refresh?: () => void;
}) {
  const items = contentNodes.map((node) => ({
    id: node.path,
    name: node.name,
    type: node.type as ContentType,
    version_path: node.version_path,
    description: node.type === 'folder' ? 'Folder' :
                 node.type === 'json' ? 'JSON' :
                 node.type === 'markdown' ? 'Markdown' :
                 node.type === 'file' ? 'File' : 'Unknown',
    is_synced: false,
    sync_source: null as string | null,
    sync_url: null as string | null,
    sync_status: 'not_connected' as const,
    last_synced_at: null as string | null,
    preview_snippet: null as string | null,
    children_count: node.children_count,
    onClick: () => {
      navigateTo(node.path.split('/').filter(Boolean), node.type || undefined);
    },
  }));

  const orderedItemIds = useMemo(() => items.map((item) => item.id), [items]);
  const gridSelection = useGridSelection({ orderedIds: orderedItemIds });

  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [bulkDeletePaths, setBulkDeletePaths] = useState<string[]>([]);
  const [bulkDeleteSubmitting, setBulkDeleteSubmitting] = useState(false);

  const openBulkDeleteDialog = useCallback(() => {
    if (gridSelection.selectedCount === 0) return;
    setBulkDeletePaths(gridSelection.selectedInOrder);
    setBulkDeleteOpen(true);
  }, [gridSelection.selectedCount, gridSelection.selectedInOrder]);

  const handleBulkDeleteConfirm = useCallback(async () => {
    if (!bulkDeletePaths.length) return;
    setBulkDeleteSubmitting(true);
    try {
      await handleBulkDelete(bulkDeletePaths);
      gridSelection.clear();
    } finally {
      setBulkDeleteSubmitting(false);
    }
  }, [bulkDeletePaths, handleBulkDelete, gridSelection]);

  useEffect(() => {
    gridSelection.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentFolderId]);

  const platformDeleteHint = useMemo(() => {
    if (typeof navigator === 'undefined') return 'Delete';
    return /Mac|iPod|iPhone|iPad/.test(navigator.platform)
      ? '⌫'
      : 'Del';
  }, []);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (gridSelection.selectedCount === 0) return;
      const tag = (event.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || (event.target as HTMLElement)?.isContentEditable) {
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        gridSelection.clear();
      } else if ((event.key === 'Delete' || event.key === 'Backspace') && !bulkDeleteOpen) {
        event.preventDefault();
        openBulkDeleteDialog();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [gridSelection, bulkDeleteOpen, openBulkDeleteDialog]);

  const handleMillerNavigate = useCallback((item: MillerColumnItem) => {
    return navigateTo(item.id.split('/').filter(Boolean), item.type || undefined);
  }, [navigateTo]);

  const handleRefresh = useCallback(async (_path: string) => {
    // Re-fetch the current view from the server. (Previously this only
    // popped a "not yet implemented" alert.) Source re-pull for synced
    // nodes is handled by the sync subsystem, not this grid action.
    refresh?.();
  }, [refresh]);

  return {
    items,
    gridSelection,
    bulkDeleteOpen,
    setBulkDeleteOpen,
    bulkDeletePaths,
    bulkDeleteSubmitting,
    openBulkDeleteDialog,
    handleBulkDeleteConfirm,
    platformDeleteHint,
    handleMillerNavigate,
    handleRefresh,
  };
}
