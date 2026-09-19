'use client';

import { useCallback, useEffect, useState } from 'react';
import { usePathResolver } from './usePathResolver';
import { useSessionValue } from '@/features/workspace/session';

type FolderBreadcrumb = { id: string; name: string };

type PendingFolderNavigation = {
  path: string | null;
  pathKey: string;
  breadcrumbs: FolderBreadcrumb[];
};

type UseDataRouteControllerArgs = {
  projectId: string;
  path: string[];
};

function buildFolderBreadcrumbs(segments: string[]): FolderBreadcrumb[] {
  return segments.map((seg, index) => ({
    id: segments.slice(0, index + 1).join('/'),
    name: seg,
  }));
}

function safeDecodePathSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

function readDataLocation(projectId: string): { path: string[]; typeHint: string } {
  if (typeof window === 'undefined') {
    return { path: [], typeHint: '' };
  }

  const prefix = `/projects/${projectId}/data`;
  const pathname = window.location.pathname;
  const rawPath = pathname.startsWith(prefix)
    ? pathname.slice(prefix.length).replace(/^\/+/, '')
    : '';

  return {
    path: rawPath
      ? rawPath.split('/').filter(Boolean).map(safeDecodePathSegment)
      : [],
    typeHint: new URLSearchParams(window.location.search).get('type') ?? '',
  };
}

function buildDataUrl(projectId: string, nextPath: string[], typeHint?: string): string {
  const encoded = nextPath.map((s) => encodeURIComponent(s)).join('/');
  const basePath = `/projects/${projectId}/data${encoded ? `/${encoded}` : ''}`;
  return typeHint ? `${basePath}?type=${encodeURIComponent(typeHint)}` : basePath;
}

export function useDataRouteController({
  projectId,
  path,
}: UseDataRouteControllerArgs) {
  const propPathKey = path.join('/');
  const [, setFilesHref] = useSessionValue('filesHref');
  // Keep routine Data navigation inside this mounted client tree. Using the
  // app router for every file click remounts the catch-all page, which makes
  // the explorer sidebar replay its whole expansion tree.
  const [clientPath, setClientPath] = useState(path);
  const [clientTypeHint, setClientTypeHint] = useState(() => readDataLocation(projectId).typeHint);
  const routePathKey = clientPath.join('/');
  useEffect(() => {
    setFilesHref(buildDataUrl(projectId, clientPath, clientTypeHint));
  }, [projectId, routePathKey, clientTypeHint, setFilesHref]); // eslint-disable-line react-hooks/exhaustive-deps
  const [pendingFolderNavigation, setPendingFolderNavigation] =
    useState<PendingFolderNavigation | null>(null);

  const {
    currentFolderId: resolvedCurrentFolderId,
    folderBreadcrumbs: resolvedFolderBreadcrumbs,
    isResolvingPath: resolvedIsResolvingPath,
    activeNodeId: resolvedActiveNodeId,
    activeNodeType: resolvedActiveNodeType,
    activePreviewType: resolvedActivePreviewType,
    activeMimeType: resolvedActiveMimeType,
    // `textContent` here is the **server-side** value (any text-like
    // file: markdown, code, yaml, csv, plaintext). The page-level
    // editor draft comes from the editor save session and may differ while dirty.
    textContent: serverTextContent,
    isLoadingText,
    readError,
    retryRead,
    markdownViewMode,
    setMarkdownViewMode,
  } = usePathResolver(projectId, clientPath, clientTypeHint);

  const shouldUsePendingFolderNavigation =
    pendingFolderNavigation !== null &&
    pendingFolderNavigation.pathKey === routePathKey;

  const currentFolderId = shouldUsePendingFolderNavigation
    ? pendingFolderNavigation.path
    : resolvedCurrentFolderId;
  const folderBreadcrumbs = shouldUsePendingFolderNavigation
    ? pendingFolderNavigation.breadcrumbs
    : resolvedFolderBreadcrumbs;
  const isResolvingPath = shouldUsePendingFolderNavigation ? false : resolvedIsResolvingPath;
  const activeNodeId = shouldUsePendingFolderNavigation ? '' : resolvedActiveNodeId;
  const activeNodeType = shouldUsePendingFolderNavigation ? '' : resolvedActiveNodeType;
  const activePreviewType = shouldUsePendingFolderNavigation ? null : resolvedActivePreviewType;
  const activeMimeType = shouldUsePendingFolderNavigation ? null : resolvedActiveMimeType;

  useEffect(() => {
    if (!pendingFolderNavigation) return;

    if (pendingFolderNavigation.pathKey !== routePathKey) {
      setPendingFolderNavigation(null);
      return;
    }

    if (
      !resolvedIsResolvingPath &&
      !resolvedActiveNodeId &&
      (resolvedCurrentFolderId ?? null) === pendingFolderNavigation.path
    ) {
      setPendingFolderNavigation(null);
    }
  }, [
    pendingFolderNavigation,
    resolvedActiveNodeId,
    resolvedCurrentFolderId,
    resolvedIsResolvingPath,
    routePathKey,
  ]);

  useEffect(() => {
    setClientPath(path);
    setClientTypeHint(readDataLocation(projectId).typeHint);
    setPendingFolderNavigation(null);
  }, [projectId, propPathKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const handlePopState = () => {
      const next = readDataLocation(projectId);
      setClientPath(next.path);
      setClientTypeHint(next.typeHint);
      setPendingFolderNavigation(null);
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [projectId]);

  const navigateTo = useCallback((nextPath: string[], typeHint?: string) => {
    const url = buildDataUrl(projectId, nextPath, typeHint);
    const nextPathKey = nextPath.join('/');

    setClientPath(nextPath);
    setClientTypeHint(typeHint ?? '');

    if (typeHint === 'folder') {
      setPendingFolderNavigation({
        path: nextPathKey || null,
        pathKey: nextPathKey,
        breadcrumbs: buildFolderBreadcrumbs(nextPath),
      });
    } else {
      setPendingFolderNavigation(null);
    }

    window.history.pushState({ puppyoneDataPath: nextPathKey }, '', url);
  }, [projectId]);

  return {
    path: clientPath,
    currentFolderId,
    folderBreadcrumbs,
    isResolvingPath,
    activeNodeId,
    activeNodeType,
    activePreviewType,
    activeMimeType,
    serverTextContent,
    isLoadingText,
    readError,
    retryRead,
    markdownViewMode,
    setMarkdownViewMode,
    navigateTo,
  };
}
