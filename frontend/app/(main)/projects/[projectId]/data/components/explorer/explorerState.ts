'use client';

import { useCallback, useSyncExternalStore } from 'react';

const expandedSet = new Set<string>();
const expandedListeners = new Set<() => void>();

function expandedSubscribe(cb: () => void) {
  expandedListeners.add(cb);
  return () => expandedListeners.delete(cb);
}

function notifyExpanded() {
  expandedListeners.forEach((cb) => cb());
}

function expansionKey(projectId: string, path: string) {
  return JSON.stringify([projectId, path]);
}

export function toggleExpanded(projectId: string, path: string) {
  const id = expansionKey(projectId, path);
  if (expandedSet.has(id)) {
    expandedSet.delete(id);
  } else {
    expandedSet.add(id);
  }
  notifyExpanded();
}

export function ensureExpanded(projectId: string, path: string) {
  const id = expansionKey(projectId, path);
  if (!expandedSet.has(id)) {
    expandedSet.add(id);
    notifyExpanded();
  }
}

export function ensureExpandedBatch(projectId: string, paths: string[]) {
  let changed = false;

  for (const path of paths) {
    const id = expansionKey(projectId, path);
    if (!expandedSet.has(id)) {
      expandedSet.add(id);
      changed = true;
    }
  }

  if (changed) notifyExpanded();
}

export function useIsExpanded(projectId: string, path: string): boolean {
  const id = expansionKey(projectId, path);
  const getSnapshot = useCallback(() => expandedSet.has(id), [id]);
  return useSyncExternalStore(expandedSubscribe, getSnapshot, getSnapshot);
}

export type PendingCreatingInfo = {
  label: string;
};

const pendingCreating = new Map<string, PendingCreatingInfo>();
const pendingCreatingListeners = new Set<() => void>();

function pendingCreatingKey(projectId: string, path: string) {
  return `${projectId}\u0000${path}`;
}

function pendingCreatingSubscribe(cb: () => void) {
  pendingCreatingListeners.add(cb);
  return () => pendingCreatingListeners.delete(cb);
}

function notifyPendingCreating() {
  pendingCreatingListeners.forEach((cb) => cb());
}

export function addPendingCreatingPath(projectId: string, path: string) {
  addPendingCreatingNode(projectId, path, { label: 'Creating folder' });
}

export function addPendingCreatingNode(
  projectId: string,
  path: string,
  info: PendingCreatingInfo,
) {
  const key = pendingCreatingKey(projectId, path);
  const current = pendingCreating.get(key);
  if (current?.label === info.label) return;
  pendingCreating.set(key, info);
  notifyPendingCreating();
}

export function removePendingCreatingPath(projectId: string, path: string) {
  const key = pendingCreatingKey(projectId, path);
  if (!pendingCreating.has(key)) return;
  pendingCreating.delete(key);
  notifyPendingCreating();
}

export function useIsPendingCreatingPath(projectId: string, path: string): boolean {
  return usePendingCreatingInfo(projectId, path) !== null;
}

export function usePendingCreatingInfo(projectId: string, path: string): PendingCreatingInfo | null {
  const getSnapshot = useCallback(
    () => pendingCreating.get(pendingCreatingKey(projectId, path)) ?? null,
    [projectId, path],
  );
  return useSyncExternalStore(pendingCreatingSubscribe, getSnapshot, getSnapshot);
}

let pendingActiveId: string | null = null;
let pendingVersion = 0;
const pendingListeners = new Set<() => void>();

export function setPendingActiveId(id: string | null) {
  pendingActiveId = id;
  pendingVersion += 1;
  pendingListeners.forEach((cb) => cb());
}

export function usePendingActiveId() {
  useSyncExternalStore(
    (cb) => {
      pendingListeners.add(cb);
      return () => pendingListeners.delete(cb);
    },
    () => pendingVersion,
    () => 0,
  );

  return pendingActiveId;
}
