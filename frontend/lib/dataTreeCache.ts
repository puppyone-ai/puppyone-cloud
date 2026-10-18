import { mutate, unstable_serialize } from 'swr';
import {
  normalizeTreePath,
  sortNodes,
  type NodeInfo,
} from './contentTreeApi';

export type DataDirectoryCacheKind = 'tree' | 'explorer-tree';
export type DataDirectoryCacheKey = readonly [DataDirectoryCacheKind, string, string, string?];

interface SWRCacheLike {
  get: (key: string) => unknown;
}

export function dataDirectoryCacheKey(
  kind: DataDirectoryCacheKind,
  projectId: string,
  dirPath: string | null | undefined,
  identity: string = 'published',
): DataDirectoryCacheKey {
  return identity === 'published' ? [kind, projectId, normalizeTreePath(dirPath)] : [kind, projectId, normalizeTreePath(dirPath), identity];
}

export function dataDirectoryCacheKeys(
  projectId: string,
  dirPath: string | null | undefined,
  identity: string = 'published',
): readonly DataDirectoryCacheKey[] {
  return [
    dataDirectoryCacheKey('tree', projectId, dirPath, identity),
    dataDirectoryCacheKey('explorer-tree', projectId, dirPath, identity),
  ];
}

export function readCachedDirectoryNodes(
  cache: SWRCacheLike,
  projectId: string,
  dirPath: string | null | undefined,
  identity: string = 'published',
): NodeInfo[] | null {
  for (const key of dataDirectoryCacheKeys(projectId, dirPath, identity)) {
    const entry = cache.get(unstable_serialize(key));
    if (Array.isArray(entry)) return entry as NodeInfo[];
    if (entry && typeof entry === 'object' && Array.isArray((entry as { data?: unknown }).data)) {
      return (entry as { data: NodeInfo[] }).data;
    }
  }
  return null;
}

export function optimisticInsertDirectoryNode(
  projectId: string,
  parentPath: string | null | undefined,
  node: NodeInfo,
  identity: string = 'published',
): void {
  for (const key of dataDirectoryCacheKeys(projectId, parentPath, identity)) {
    mutate(
      key,
      (prev?: NodeInfo[]) => {
        const existing = prev ?? [];
        if (existing.some((n) => n.path === node.path)) return existing;
        return sortNodes([...existing, node]);
      },
      { revalidate: false },
    );
  }
}

export function optimisticRemoveDirectoryNode(
  projectId: string,
  parentPath: string | null | undefined,
  nodePath: string,
  identity: string = 'published',
): void {
  for (const key of dataDirectoryCacheKeys(projectId, parentPath, identity)) {
    mutate(
      key,
      (prev?: NodeInfo[]) => (prev ?? []).filter((node) => node.path !== nodePath),
      { revalidate: false },
    );
  }
}

export function optimisticSeedEmptyDirectory(
  projectId: string,
  dirPath: string | null | undefined,
  identity: string = 'published',
): void {
  for (const key of dataDirectoryCacheKeys(projectId, dirPath, identity)) {
    mutate(
      key,
      (prev?: NodeInfo[]) => prev ?? [],
      { revalidate: false },
    );
  }
}
