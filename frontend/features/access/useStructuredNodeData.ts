'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  refreshToolsByPath,
  useTable,
  useToolsByPath,
} from '@/lib/hooks/useData';
import {
  createTool,
  deleteTool,
  type AccessPoint,
  type McpToolPermissions,
  type McpToolType,
  type Tool,
} from '@/lib/mcpApi';
import type { NodeInfo } from '@/lib/contentTreeApi';

const TOOL_TYPES: McpToolType[] = [
  'search',
  'query_data',
  'get_all_data',
  'create',
  'update',
  'delete',
];

function basenameFromPath(path: string): string {
  return path.split('/').filter(Boolean).pop() || path;
}

function normalizeJsonPath(path: string) {
  if (!path || path === '/') return '';
  return path;
}

export function useStructuredNodeData({
  projectId,
  activeNodeId,
  activeNodeType,
  activeFormatDefaultViewer,
  contentNodes,
}: {
  projectId: string;
  activeNodeId: string;
  activeNodeType: string;
  activeFormatDefaultViewer?: string | null;
  contentNodes: NodeInfo[];
}) {
  const shouldLoadStructuredTableData =
    Boolean(activeNodeId) &&
    (activeNodeType === 'github' || activeFormatDefaultViewer === 'json-table');
  const { tools: tableTools, isLoading: toolsLoading, error: toolsError } = useToolsByPath(
    shouldLoadStructuredTableData ? activeNodeId : undefined,
    projectId,
  );
  const { tableData: loadedTableData, refresh: refreshTable } = useTable(
    projectId,
    shouldLoadStructuredTableData ? activeNodeId : undefined,
  );
  const currentTableData = shouldLoadStructuredTableData ? loadedTableData : undefined;
  const activeNodeDisplayName = activeNodeId ? basenameFromPath(activeNodeId) : '';

  const [accessPoints, setAccessPoints] = useState<AccessPoint[]>([]);
  const lastSyncedTableKey = useRef<string | null>(null);
  const tableKey = JSON.stringify([projectId, activeNodeId]);

  const tableNameById = useMemo(() => {
    const map: Record<string, string> = {};
    contentNodes.forEach((node) => { map[node.path || node.id] = node.name; });
    if (currentTableData?.id && currentTableData?.name) {
      map[currentTableData.id] = currentTableData.name;
    }
    return map;
  }, [contentNodes, currentTableData?.id, currentTableData?.name]);

  useEffect(() => {
    if (!shouldLoadStructuredTableData || !activeNodeId || toolsLoading || toolsError) {
      // Disabled/loading queries may return a fresh fallback array. A reset
      // must be idempotent even when an upstream dependency changes identity.
      setAccessPoints(current => current.length === 0 ? current : []);
      lastSyncedTableKey.current = null;
      return;
    }
    if (tableKey === lastSyncedTableKey.current) return;

    const pathPermissionsMap = new Map<string, McpToolPermissions>();
    tableTools.forEach((tool) => {
      const toolPath = tool.json_path || '';
      const existing = pathPermissionsMap.get(toolPath) || {};
      pathPermissionsMap.set(toolPath, { ...existing, [tool.type]: true });
    });

    const initialAccessPoints: AccessPoint[] = [];
    pathPermissionsMap.forEach((permissions, toolPath) => {
      initialAccessPoints.push({ id: `saved-${toolPath || 'root'}`, path: toolPath, permissions });
    });
    setAccessPoints(initialAccessPoints);
    lastSyncedTableKey.current = tableKey;
  }, [activeNodeId, tableKey, shouldLoadStructuredTableData, toolsLoading, toolsError, tableTools]);

  async function syncToolsForPath(params: {
    versionPath: string;
    path: string;
    permissions: McpToolPermissions;
    existingTools: Tool[];
  }) {
    const { versionPath, path: toolPath, permissions, existingTools } = params;
    const jsonPath = normalizeJsonPath(toolPath);
    const byType = new Map<string, Tool>();
    for (const tool of existingTools) {
      if (tool.path !== versionPath) continue;
      if ((tool.json_path || '') !== jsonPath) continue;
      const toolType = tool.type as string;
      if (toolType === 'shell_access' || toolType === 'shell_access_readonly') continue;
      byType.set(tool.type, tool);
    }
    const effectivePermissions: Record<string, boolean> = { ...(permissions as any) };
    const toDelete: string[] = [];
    const toCreate: McpToolType[] = [];
    for (const type of TOOL_TYPES) {
      const enabled = !!effectivePermissions[type];
      const existing = byType.get(type);
      if (!enabled && existing) toDelete.push(existing.id);
      if (enabled && !existing) toCreate.push(type);
    }
    for (const id of toDelete) await deleteTool(id);
    for (const type of toCreate) {
      await createTool({
        path: versionPath,
        json_path: jsonPath,
        type,
        name: `${type}_${versionPath}_${jsonPath ? jsonPath.replaceAll('/', '_') : 'root'}`,
        description: undefined,
      });
    }
  }

  async function deleteAllToolsForPath(params: {
    versionPath: string;
    path: string;
    existingTools: Tool[];
  }) {
    const { versionPath, path: toolPath, existingTools } = params;
    const jsonPath = normalizeJsonPath(toolPath);
    const toDelete = existingTools.filter((tool) =>
      tool.path === versionPath && (tool.json_path || '') === jsonPath
    );
    for (const tool of toDelete) await deleteTool(tool.id);
  }

  const configuredAccessPoints = useMemo(() =>
    accessPoints.map((accessPoint) => ({
      path: accessPoint.path,
      permissions: accessPoint.permissions,
    })),
  [accessPoints]);

  const refreshToolsForActiveNode = () => {
    if (!activeNodeId || !shouldLoadStructuredTableData) return;
    refreshToolsByPath(activeNodeId, projectId);
  };

  return {
    tableTools,
    currentTableData,
    refreshTable,
    shouldLoadStructuredTableData,
    activeNodeDisplayName,
    accessPoints,
    setAccessPoints,
    configuredAccessPoints,
    tableNameById,
    syncToolsForPath,
    deleteAllToolsForPath,
    refreshToolsForActiveNode,
  };
}
