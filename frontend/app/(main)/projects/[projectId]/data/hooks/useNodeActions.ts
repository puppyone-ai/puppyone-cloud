'use client';

import { useState, useRef, useCallback } from 'react';
import { mutate } from 'swr';
import { downloadNode, moveFile, removeFile, bulkRemoveFiles, type NodeInfo } from '@/lib/contentTreeApi';
import { refreshFolderNodes, refreshProjectHistory } from '@/lib/hooks/useData';
import { dataDirectoryCacheKeys } from '@/lib/dataTreeCache';
import { ensureExpanded } from '../components/explorer';

export type DataPageToastType = 'success' | 'error' | 'loading';
export type DataPageToast = { message: string; type: DataPageToastType };
export type PendingMoveConfirm = {
  nodePath: string;
  nodeName: string;
  sourceParentPath: string | null;
  targetFolderPath: string | null;
  newPath: string;
  targetLabel: string;
};

function parentOf(path: string): string {
  return path.includes('/') ? path.substring(0, path.lastIndexOf('/')) : '';
}

function normalizeTreePath(path: string): string {
  return path.trim().replace(/^\/+|\/+$/g, '');
}

function isSameOrDescendantPath(parent: string, child: string | null): boolean {
  const cleanParent = normalizeTreePath(parent);
  if (!cleanParent) return false;
  const cleanChild = child ? normalizeTreePath(child) : '';
  return cleanChild === cleanParent || cleanChild.startsWith(`${cleanParent}/`);
}

function basename(path: string): string {
  const clean = normalizeTreePath(path);
  return clean.includes('/') ? clean.substring(clean.lastIndexOf('/') + 1) : clean;
}

function moveTargetLabel(path: string | null): string {
  return path ? `"${path}"` : 'Project Root';
}

function matchesDeletedRoot(path: string, deletedRoots: readonly string[]): boolean {
  const clean = normalizeTreePath(path);
  return deletedRoots.some((root) => clean === root || clean.startsWith(`${root}/`));
}

function cacheItemPath(item: unknown): string {
  const maybe = item as { path?: unknown; id?: unknown };
  if (typeof maybe.path === 'string') return maybe.path;
  if (typeof maybe.id === 'string') return maybe.id;
  return '';
}

function removeDeletedFromTreeCache(current: unknown, deletedRoots: readonly string[]): unknown {
  if (!Array.isArray(current)) return current;
  return current.filter((item) => {
    const path = cacheItemPath(item);
    return !path || !matchesDeletedRoot(path, deletedRoots);
  });
}

function hideDeletedPathsInTreeCaches(projectId: string, paths: readonly string[]): void {
  const deletedRoots = collapseDescendantPaths([...paths]);
  if (!deletedRoots.length) return;
  void mutate(
    (key) => Array.isArray(key) && key[0] === 'tree' && key[1] === projectId,
    (current: unknown) => removeDeletedFromTreeCache(current, deletedRoots),
    { revalidate: false },
  );
}

function revalidateProjectTreeCaches(projectId: string): void {
  void mutate(
    (key) => Array.isArray(key) && key[0] === 'tree' && key[1] === projectId,
    undefined,
    { revalidate: true },
  );
}

function refreshDeletedParentFolders(
  projectId: string,
  deletedPaths: readonly string[],
  parents: readonly string[],
): void {
  void refreshFolderNodes(projectId, ...parents)
    .catch(() => undefined)
    .finally(() => hideDeletedPathsInTreeCaches(projectId, deletedPaths));
}

function collapseDescendantPaths(paths: string[]): string[] {
  const clean = Array.from(new Set(
    paths
      .map(normalizeTreePath)
      .filter(Boolean),
  ));

  clean.sort((a, b) => {
    const depth = a.split('/').length - b.split('/').length;
    return depth || a.localeCompare(b);
  });

  const roots: string[] = [];
  for (const path of clean) {
    if (roots.some((root) => path === root || path.startsWith(`${root}/`))) {
      continue;
    }
    roots.push(path);
  }
  return roots;
}

export function useNodeActions(projectId: string, currentFolderPath: string | null) {
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<{ id: string; name: string } | null>(null);
  const [renameError, setRenameError] = useState<string | null>(null);

  const [moveDialogTarget, setMoveDialogTarget] = useState<{ id: string; name: string; version_path?: string } | null>(null);
  const [moveConfirmTarget, setMoveConfirmTarget] = useState<PendingMoveConfirm | null>(null);
  const [deleteDialogTarget, setDeleteDialogTarget] = useState<{ id: string; name: string } | null>(null);

  const [toast, setToast] = useState<DataPageToast | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const deletingPathsRef = useRef<Set<string>>(new Set());

  const showToast = useCallback((
    message: string,
    type: DataPageToastType = 'success',
    durationMs: number | null = 3000,
  ) => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToast({ message, type });
    if (durationMs !== null) {
      toastTimerRef.current = setTimeout(() => setToast(null), durationMs);
    } else {
      toastTimerRef.current = null;
    }
  }, []);

  const [toolPanelTarget, setToolPanelTarget] = useState<{ id: string; name: string; type: string; jsonPath?: string } | null>(null);

  const handleRename = useCallback((path: string, currentName: string) => {
    setRenameTarget({ id: path, name: currentName });
    setRenameError(null);
    setRenameDialogOpen(true);
  }, []);

  const handleRenameConfirm = useCallback(async (newName: string) => {
    if (!renameTarget) return;
    setRenameError(null);
    try {
      const oldPath = renameTarget.id;
      const parentDir = parentOf(oldPath);
      const newPath = parentDir ? `${parentDir}/${newName}` : newName;
      showToast(`Renaming "${renameTarget.name}"...`, 'loading', null);
      await moveFile(projectId, oldPath, newPath);
      // Same parent folder, only one listing to refresh.
      void refreshFolderNodes(projectId, parentDir);
      refreshProjectHistory(projectId);
      setRenameDialogOpen(false);
      setRenameTarget(null);
      showToast(`Renamed to "${newName}"`);
    } catch (err: unknown) {
      console.error('Failed to rename:', err);
      const errorObj = err as { message?: string };
      setRenameError(errorObj?.message || 'Failed to rename item');
      showToast(errorObj?.message || 'Failed to rename item', 'error');
      throw err;
    }
  }, [renameTarget, projectId, showToast]);

  const deleteSinglePath = useCallback(async (path: string, name: string) => {
    const cleanPath = normalizeTreePath(path);
    if (deletingPathsRef.current.has(cleanPath)) {
      const error = new Error(`Still deleting "${name}"...`);
      showToast(error.message, 'error');
      throw error;
    }
    const parent = parentOf(cleanPath);
    try {
      deletingPathsRef.current.add(cleanPath);
      hideDeletedPathsInTreeCaches(projectId, [cleanPath]);
      showToast(`Deleting "${name}"...`, 'loading', null);
      await removeFile(projectId, cleanPath);
      hideDeletedPathsInTreeCaches(projectId, [cleanPath]);
      refreshDeletedParentFolders(projectId, [cleanPath], [parent]);
      refreshProjectHistory(projectId);
      showToast(`Deleted "${name}"`);
    } catch (err) {
      console.error('Failed to delete:', err);
      revalidateProjectTreeCaches(projectId);
      const msg = (err as { message?: string })?.message || 'Failed to delete item';
      showToast(msg, 'error');
      throw err;
    } finally {
      deletingPathsRef.current.delete(cleanPath);
    }
  }, [projectId, showToast]);

  const handleDelete = useCallback((path: string, name: string) => {
    setDeleteDialogTarget({ id: path, name });
  }, []);

  const closeDeleteDialog = useCallback(() => {
    setDeleteDialogTarget(null);
  }, []);

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteDialogTarget) return;
    await deleteSinglePath(deleteDialogTarget.id, deleteDialogTarget.name);
  }, [deleteDialogTarget, deleteSinglePath]);

  /**
   * Multi-select bulk delete.
   *
   * A product-level delete action should stay one request from the
   * browser's point of view. If selection contains both a folder and
   * its descendants, submit only the folder root so the backend can
   * unlink the subtree as one versioned operation.
   */
  const handleBulkDelete = useCallback(async (paths: string[]): Promise<void> => {
    const clean = collapseDescendantPaths(paths);
    if (!clean.length) return;
    const inFlight = clean.filter((p) => deletingPathsRef.current.has(p));
    if (inFlight.length) {
      showToast(`${inFlight.length} item(s) still deleting...`, 'error');
      return;
    }
    const parents = Array.from(new Set(clean.map(parentOf)));
    try {
      clean.forEach((p) => deletingPathsRef.current.add(p));
      hideDeletedPathsInTreeCaches(projectId, clean);
      showToast(`Deleting ${clean.length} item(s)...`, 'loading', null);
      await bulkRemoveFiles(projectId, clean);
      hideDeletedPathsInTreeCaches(projectId, clean);
      // Each unique parent listing changed; rest of the tree is untouched.
      refreshDeletedParentFolders(projectId, clean, parents);
      refreshProjectHistory(projectId);
      showToast(`Deleted ${clean.length} item(s)`);
    } catch (err) {
      console.error('Failed to bulk delete:', err);
      revalidateProjectTreeCaches(projectId);
      const msg = (err as { message?: string })?.message || 'Failed to delete items';
      showToast(msg, 'error');
      throw err;
    } finally {
      clean.forEach((p) => deletingPathsRef.current.delete(p));
    }
  }, [projectId, showToast]);

  const handleDownload = useCallback(async (path: string, name: string) => {
    // App-layer toast covers only the "preparing" window — the brief gap
    // between the user clicking and the server starting to stream bytes.
    // Once the browser's native download manager picks up the response
    // it owns the rest (progress bar, pause/cancel, "Show in Finder"),
    // so we don't show a "Downloaded" follow-up — that would just
    // duplicate what the browser is already telling them.
    try {
      showToast(`Preparing "${name}"...`);
      await downloadNode(projectId, path);
    } catch (err) {
      console.error('Failed to download:', err);
      const msg = (err as { message?: string })?.message || 'Failed to download';
      showToast(msg, 'error');
    }
  }, [projectId, showToast]);

  const executeMoveNode = useCallback(async (
    nodePath: string,
    targetFolderPath: string | null,
    sourceParentPath: string | null = currentFolderPath,
  ) => {
    const cleanNodePath = normalizeTreePath(nodePath);
    const cleanTargetPath = targetFolderPath ? normalizeTreePath(targetFolderPath) : null;
    const cleanSourceParent = sourceParentPath ? normalizeTreePath(sourceParentPath) : null;
    let movedNode: NodeInfo | undefined;

    // Update both read models optimistically — `tree` backs the main content
    // pane and `explorer-tree` backs the sidebar. Mutating only `tree` left the
    // sidebar showing the node in its old folder until revalidation landed.
    for (const sourceKey of dataDirectoryCacheKeys(projectId, cleanSourceParent)) {
      mutate(
        sourceKey,
        (nodes: NodeInfo[] | undefined) => {
          const found = (nodes ?? []).find(n => n.path === cleanNodePath || n.id === cleanNodePath);
          if (found) movedNode = found;
          return (nodes ?? []).filter(n => n.path !== cleanNodePath && n.id !== cleanNodePath);
        },
        { revalidate: false },
      );
    }

    if (movedNode) {
      const name = movedNode.name;
      const newPath = cleanTargetPath ? `${cleanTargetPath}/${name}` : name;
      const nodeForTarget = { ...movedNode, path: newPath, id: newPath, parent_id: cleanTargetPath };
      if (cleanTargetPath) ensureExpanded(projectId, cleanTargetPath);
      for (const targetKey of dataDirectoryCacheKeys(projectId, cleanTargetPath)) {
        mutate(
          targetKey,
          (nodes: NodeInfo[] | undefined) => nodes ? [...nodes, nodeForTarget] : undefined,
          { revalidate: false },
        );
      }
    }

    try {
      const name = basename(cleanNodePath);
      showToast(`Moving "${name}"...`, 'loading', null);
      const newPath = cleanTargetPath ? `${cleanTargetPath}/${name}` : name;
      await moveFile(projectId, cleanNodePath, newPath);
      // Source parent + target parent are the only two listings that changed.
      void refreshFolderNodes(projectId, cleanSourceParent, cleanTargetPath);
      refreshProjectHistory(projectId);
      showToast(`Moved "${name}"`);
    } catch (err: unknown) {
      refreshFolderNodes(projectId, cleanSourceParent, cleanTargetPath);
      const msg = (err as { message?: string })?.message || 'Failed to move item';
      showToast(msg, 'error');
      throw err;
    }
  }, [projectId, currentFolderPath, showToast]);

  const handleMoveNode = useCallback(async (
    nodePath: string,
    targetFolderPath: string | null,
    sourceParentPath: string | null = currentFolderPath,
  ) => {
    const cleanNodePath = normalizeTreePath(nodePath);
    const cleanTargetPath = targetFolderPath ? normalizeTreePath(targetFolderPath) : null;
    const cleanSourceParent = sourceParentPath ? normalizeTreePath(sourceParentPath) : null;
    if (!cleanNodePath || cleanSourceParent === cleanTargetPath) return;
    if (isSameOrDescendantPath(cleanNodePath, cleanTargetPath)) {
      showToast('Cannot move an item into itself or its child folder', 'error');
      return;
    }

    const name = basename(cleanNodePath);
    const newPath = cleanTargetPath ? `${cleanTargetPath}/${name}` : name;
    setMoveConfirmTarget({
      nodePath: cleanNodePath,
      nodeName: name,
      sourceParentPath: cleanSourceParent,
      targetFolderPath: cleanTargetPath,
      newPath,
      targetLabel: moveTargetLabel(cleanTargetPath),
    });
  }, [currentFolderPath, showToast]);

  const closeMoveConfirm = useCallback(() => {
    setMoveConfirmTarget(null);
  }, []);

  const handleMoveConfirm = useCallback(async () => {
    if (!moveConfirmTarget) return;
    await executeMoveNode(
      moveConfirmTarget.nodePath,
      moveConfirmTarget.targetFolderPath,
      moveConfirmTarget.sourceParentPath,
    );
  }, [executeMoveNode, moveConfirmTarget]);

  const handleMoveRequest = useCallback((path: string, name: string, version_path?: string) => {
    setMoveDialogTarget({ id: path, name, version_path });
  }, []);

  const handleCreateTool = useCallback((path: string, name: string, type: string, jsonPath?: string) => {
    setToolPanelTarget({ id: path, name, type, jsonPath });
  }, []);

  const closeRenameDialog = useCallback(() => {
    setRenameDialogOpen(false);
    setRenameTarget(null);
    setRenameError(null);
  }, []);

  return {
    renameDialogOpen, renameTarget, renameError,
    handleRename, handleRenameConfirm, closeRenameDialog,
    moveDialogTarget, setMoveDialogTarget,
    moveConfirmTarget, closeMoveConfirm, handleMoveConfirm,
    deleteDialogTarget, closeDeleteDialog, handleDeleteConfirm,
    handleMoveNode, handleMoveRequest,
    toast, showToast,
    toolPanelTarget, setToolPanelTarget,
    handleDelete, handleDownload, handleCreateTool,
    handleBulkDelete,
  };
}
