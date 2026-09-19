'use client';

import { useExplorerActions } from '@/features/files/explorerSession';

import { useProjectSession } from '@/features/workspace/session';

import type { MouseEvent as ReactMouseEvent } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { AccessResource } from '@/contexts/AgentContext';
import {
  directChildrenOf,
  listDir,
  mkdir,
  writeFile,
  type NodeInfo,
  type NodeType,
} from '@/lib/contentTreeApi';
import {
  optimisticInsertDirectoryNode,
  optimisticRemoveDirectoryNode,
  optimisticSeedEmptyDirectory,
  readCachedDirectoryNodes,
} from '@/lib/dataTreeCache';
import { refreshFolderNodes, refreshProjectHistory } from '@/lib/hooks/useData';
import { useSWRConfig } from 'swr';

const CREATE_FOLDER_VERIFY_DELAYS_MS = [800, 1800, 3500, 7000];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isAmbiguousWriteFailure(err: unknown): boolean {
  const maybe = err as {
    isNetworkError?: boolean;
    code?: unknown;
    status?: unknown;
    name?: unknown;
  } | null;

  if (!maybe) return false;
  return (
    maybe.isNetworkError === true ||
    maybe.code === 'NETWORK_ERROR' ||
    maybe.status === 0 ||
    maybe.name === 'AbortError' ||
    err instanceof TypeError
  );
}

async function parentContainsFolder(
  projectId: string,
  parentPath: string,
  folderPath: string,
): Promise<boolean> {
  const listing = await listDir(projectId, parentPath, { timeoutMs: 10_000 });
  return directChildrenOf(listing.nodes, parentPath).some(
    (node) => node.path === folderPath && node.type === 'folder',
  );
}

function recoverAmbiguousFolderCreate(args: {
  readonly removePendingCreatingPath: (projectId: string, path: string) => void;
  readonly projectId: string;
  readonly parentPath: string;
  readonly folderPath: string;
  readonly folderName: string;
  readonly showToast?: (
    message: string,
    type?: 'success' | 'error' | 'loading',
    durationMs?: number | null,
  ) => void;
}): void {
  const { projectId, parentPath, folderPath, folderName, showToast, removePendingCreatingPath } = args;
  let settled = false;

  const verify = async () => {
    if (settled) return;
    let reachedServerWithoutFolder = false;
    for (const delay of CREATE_FOLDER_VERIFY_DELAYS_MS) {
      await sleep(delay);
      if (settled) return;
      try {
        if (await parentContainsFolder(projectId, parentPath, folderPath)) {
          await refreshFolderNodes(projectId, parentPath, folderPath);
          void refreshProjectHistory(projectId);
          removePendingCreatingPath(projectId, folderPath);
          showToast?.(`Created "${folderName}"`);
          settled = true;
          return;
        }
        reachedServerWithoutFolder = true;
      } catch {
        // Still offline or the backend is unreachable. Keep the optimistic row:
        // the write may already have crossed the server's publish boundary.
      }
    }

    if (reachedServerWithoutFolder) {
      optimisticRemoveDirectoryNode(projectId, parentPath, folderPath);
      await refreshFolderNodes(projectId, parentPath).catch(() => undefined);
      removePendingCreatingPath(projectId, folderPath);
      showToast?.(`Failed to create "${folderName}"`, 'error');
      settled = true;
      return;
    }

    showToast?.(`Still checking "${folderName}"...`, 'loading', 5000);
  };

  void verify();

  if (typeof window !== 'undefined') {
    window.addEventListener(
      'online',
      () => {
        if (!settled) void verify();
      },
      { once: true },
    );
  }
}

/**
 * Build a placeholder NodeInfo for optimistic insertion into the tree
 * before the backend write completes (instant feedback).
 *
 * After the write the caller issues refreshFolderNodes which re-fetches just
 * the affected parent folder listing. If the network drops after the server may
 * have accepted the write, the caller keeps this row pending until a canonical
 * listing confirms whether the folder exists.
 */
function buildOptimisticNode(
  args: {
    readonly projectId: string;
    readonly path: string;
    readonly name: string;
    readonly type: NodeType;
  },
): NodeInfo {
  const { projectId, path, name, type } = args;
  const parentPath = path.includes('/')
    ? path.substring(0, path.lastIndexOf('/'))
    : '';
  const nowIso = new Date().toISOString();
  return {
    name,
    path,
    type,
    content_hash: null,
    size_bytes: 0,
    mime_type: null,
    children_count: type === 'folder' ? 0 : null,
    integrity_status: 'ok',
    id: path,
    version_path: `/${path}`,
    parent_id: parentPath || null,
    project_id: projectId,
    is_synced: false,
    sync_source: null,
    sync_url: null,
    sync_status: 'not_connected',
    sync_config: null,
    last_synced_at: null,
    preview_snippet: null,
    created_at: nowIso,
    updated_at: nowIso,
  };
}

export interface CreateMenuPosition {
  x: number;
  y: number;
  anchorLeft: number;
}

export interface DataCreateMenuActions {
  onClose: () => void;
  onCreateFolder: () => Promise<void>;
  onCreateBlankJson: () => Promise<void>;
  onCreateBlankMarkdown: () => Promise<void>;
  onImportFromFiles: () => void;
  onImportFromUrl: () => void;
  onImportFromSaas: () => void;
  onImportNotion: () => void;
  onConnectGitHub: () => void;
  onImportGmail: () => void;
  onImportDocs: () => void;
  onImportCalendar: () => void;
  onImportSheets: () => void;
  onConnectSupabase: () => void;
  onImportSearchConsole: () => void;
  onCreateAgent: () => void;
  onCreateMcp: () => void;
  onCreateSandbox: () => void;
  onCreateSshTerminal: () => void;
}

export type CreateMenuActionSource = 'create' | 'access';

interface UseDataCreateFlowOptions {
  projectId: string;
  currentFolderId: string | null;
  navigateTo: (nextPath: string[], typeHint?: string) => void;
  openSyncCreatePanel: (targetScopePath?: string | null) => void;
  openSyncSetting: (provider: string, target?: AccessResource) => void;
  openFilePickerForTarget?: (target: { path: string | null; name: string }) => void;
  openFileImportDialogForTarget?: (target: { path: string | null; name: string }) => void;
  showToast?: (
    message: string,
    type?: 'success' | 'error' | 'loading',
    durationMs?: number | null,
  ) => void;
}

function folderDisplayName(path: string | null): string {
  if (!path) return 'Root';
  const parts = path.split('/').filter(Boolean);
  return parts.at(-1) ?? 'Root';
}

function getCreateMenuOpenId(
  targetFolderId: string | null | undefined,
  currentFolderId: string | null,
) {
  if (targetFolderId === null) return '__root__';
  if (targetFolderId === undefined) return currentFolderId ?? '__root__';
  return targetFolderId;
}

export function useDataCreateFlow({
  projectId,
  currentFolderId,
  navigateTo,
  openSyncCreatePanel,
  openSyncSetting,
  openFilePickerForTarget,
  openFileImportDialogForTarget,
  showToast,
}: UseDataCreateFlowOptions) {
  const { addPendingCreatingNode, addPendingCreatingPath, ensureExpanded, removePendingCreatingPath } = useExplorerActions();
  const { cache } = useSWRConfig();
  const openPanel = useProjectSession(state => state.openPanel);
  const [createTableOpen, setCreateTableOpen] = useState(false);
  const [defaultStartOption, setDefaultStartOption] = useState<'documents' | 'url'>('documents');
  const [createMenuOpen, setCreateMenuOpen] = useState(false);
  const [createMenuPosition, setCreateMenuPosition] = useState<CreateMenuPosition | null>(null);
  const [createInFolderId, setCreateInFolderId] = useState<string | null | undefined>(undefined);
  // Two pieces of state for the scoped expose flow.  When a user
  // chooses `Expose as...`, we open the same CreateMenu instance
  // but flip it into accessOnly mode (only renders the New Access
  // submenu content, flat) and stash the folder path that should
  // be pre-filled as the target on whichever provider the user
  // picks from the menu.  Reusing the existing create-menu state
  // machine — same position / outside-click-to-close /
  // reposition-on-scroll behaviour — instead of standing up a
  // second menu instance.
  const [createMenuAccessOnly, setCreateMenuAccessOnly] = useState(false);
  const [accessTargetPath, setAccessTargetPath] = useState<string | null>(null);
  const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null);
  const createMenuRef = useRef<HTMLDivElement>(null);
  const createMenuTriggerRef = useRef<HTMLElement | null>(null);
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout>>();

  const createMenuOpenForId = createMenuOpen
    ? getCreateMenuOpenId(createInFolderId, currentFolderId)
    : undefined;
  const createMenuOpenAction: CreateMenuActionSource | null = createMenuOpen
    ? createMenuAccessOnly ? 'access' : 'create'
    : null;

  const highlightCreatedNode = useCallback((nodeId: string) => {
    if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
    setHighlightNodeId(nodeId);
    highlightTimerRef.current = setTimeout(() => setHighlightNodeId(null), 2500);
  }, []);

  const updateCreateMenuPosition = useCallback((triggerEl: HTMLElement) => {
    const triggerRect = triggerEl.getBoundingClientRect();
    const hostRect =
      triggerEl.closest('[data-menu-host="true"]')?.getBoundingClientRect() ?? triggerRect;

    setCreateMenuPosition({
      x: triggerRect.left,
      y: hostRect.bottom - 1,
      anchorLeft: triggerRect.left,
    });
  }, []);

  const closeCreateMenu = useCallback(() => {
    setCreateMenuOpen(false);
    setCreateMenuAccessOnly(false);
    setAccessTargetPath(null);
    createMenuTriggerRef.current = null;
  }, []);

  const openCreateMenu = useCallback(
    (event: ReactMouseEvent<Element>, parentId: string | null | undefined) => {
      event.preventDefault();
      event.stopPropagation();

      const triggerEl = event.currentTarget as HTMLElement;
      const nextOpenId = getCreateMenuOpenId(parentId, currentFolderId);
      const sameTrigger =
        createMenuOpen &&
        createMenuTriggerRef.current === triggerEl &&
        createMenuOpenForId === nextOpenId;

      if (sameTrigger) {
        closeCreateMenu();
        return;
      }

      createMenuTriggerRef.current = triggerEl;
      updateCreateMenuPosition(triggerEl);
      setCreateInFolderId(parentId);
      setCreateMenuOpen(true);
    },
    [
      closeCreateMenu,
      createMenuOpen,
      createMenuOpenForId,
      currentFolderId,
      updateCreateMenuPosition,
    ],
  );

  const handleCreateClick = useCallback(
    (event: ReactMouseEvent<Element>) => {
      openCreateMenu(event, undefined);
    },
    [openCreateMenu],
  );

  const handleMillerCreateClick = useCallback(
    (event: ReactMouseEvent<Element>, parentId: string | null) => {
      setCreateMenuAccessOnly(false);
      setAccessTargetPath(null);
      openCreateMenu(event, parentId);
    },
    [openCreateMenu],
  );

  // Per-folder plug-button entry point.  Same menu, but accessOnly
  // mode → flat list of providers/agents/endpoints, no Create-Blank
  // / Upload sections.  Stashes `folderPath` so the picker callbacks
  // below can pre-bind it as the target resource on whichever
  // provider the user picks.  The folder path is normalised the
  // same way as in page.tsx's openSyncCreatePanelForFolder ('' for
  // project root, raw path otherwise) so AgentContext's
  // `setDraftResources` keys line up with `accessByPath`.
  const handleAccessMenuClick = useCallback(
    (event: ReactMouseEvent<Element>, folderPath: string) => {
      setCreateMenuAccessOnly(true);
      setAccessTargetPath(folderPath);
      openCreateMenu(event, folderPath || null);
    },
    [openCreateMenu],
  );

  useEffect(() => {
    if (!createMenuOpen) return;

    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (createMenuRef.current?.contains(target)) return;
      if (createMenuTriggerRef.current?.contains(target)) return;
      closeCreateMenu();
    };

    const handleReposition = () => {
      const triggerEl = createMenuTriggerRef.current;
      if (!triggerEl) {
        closeCreateMenu();
        return;
      }
      updateCreateMenuPosition(triggerEl);
    };

    document.addEventListener('mousedown', handlePointerDown);
    window.addEventListener('resize', handleReposition);
    window.addEventListener('scroll', handleReposition, true);

    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      window.removeEventListener('resize', handleReposition);
      window.removeEventListener('scroll', handleReposition, true);
    };
  }, [closeCreateMenu, createMenuOpen, updateCreateMenuPosition]);

  useEffect(
    () => () => {
      if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
    },
    [],
  );

  const PROVIDER_NODE_TYPE: Record<string, 'json' | 'markdown' | 'folder'> = {
    gmail: 'json',
    calendar: 'json',
    sheets: 'json',
    linear: 'json',
    supabase: 'json',
    docs: 'markdown',
    notion: 'folder',
  };

  const PROVIDER_DEFAULT_NAMES: Record<string, string> = {
    gmail: 'Gmail Inbox',
    calendar: 'Calendar Events',
    sheets: 'Sheet Data',
    linear: 'Linear Issues',
    docs: 'Document',
    notion: 'Notion Pages',
    supabase: 'Supabase Data',
  };

  const handleCreateAndSync = useCallback(
    async (saasProvider: string) => {
      const nodeType = PROVIDER_NODE_TYPE[saasProvider];
      if (!nodeType) {
        openSyncSetting(saasProvider);
        openSyncCreatePanel();
        return;
      }

      const name = PROVIDER_DEFAULT_NAMES[saasProvider] || 'Untitled';
      const parentPath = currentFolderId ?? '';

      try {
        const fullPath = parentPath ? `${parentPath}/${name}` : name;
        showToast?.(`Creating "${name}"...`, 'loading', null);

        if (nodeType === 'json') {
          await writeFile(projectId, fullPath, null, 'json');
        } else if (nodeType === 'markdown') {
          await writeFile(projectId, fullPath, '', 'markdown');
        } else {
          await mkdir(projectId, fullPath);
        }

        await refreshFolderNodes(projectId, parentPath);
        void refreshProjectHistory(projectId);
        const resourceNodeType: 'folder' | 'json' | 'file' =
          nodeType === 'markdown' ? 'file' : nodeType;

        openSyncSetting(saasProvider, {
          path: fullPath,
          nodeName: name,
          nodeType: resourceNodeType,
          readonly: true,
        });
        openSyncCreatePanel();
        showToast?.(`Created "${name}"`);
      } catch {
        showToast?.(`Failed to create "${name}"`, 'error');
        openSyncSetting(saasProvider);
        openSyncCreatePanel();
      }
    },
    [currentFolderId, openSyncCreatePanel, openSyncSetting, projectId, showToast],
  );

  // Provider-pick handler that branches on which menu launched it:
  //
  //   - plug menu (accessTargetPath !== null) → the user already
  //     picked a target by clicking the folder's plug, so skip the
  //     "create a node + bind it" dance.  Build the AccessResource
  //     directly from the prefilled folder path and seed it via
  //     `openSyncSetting`'s `preBindResource` argument.  The panel
  //     auto-jumps to the provider's config view (because
  //     pendingSyncProvider is set) with the target chip already
  //     populated (because draftResources[0] is set).
  //
  //   - + menu (accessTargetPath === null) → the user is starting
  //     from "create something new under this folder", so go through
  //     the existing handleCreateAndSync, which mints a new node
  //     (Notion / Gmail / etc. parent folder) and binds it as the
  //     target.
  //
  // Same provider id strings flow into both branches, so the panel's
  // pendingSyncProvider effect lights up the right config view either
  // way.
  const handleAccessSelect = useCallback(
    (provider: string) => {
      if (accessTargetPath !== null) {
        const segs = accessTargetPath.split('/').filter(Boolean);
        const nodeName = segs.length > 0 ? segs[segs.length - 1] : 'Root';
        openSyncSetting(provider, {
          path: accessTargetPath,
          nodeName,
          nodeType: 'folder',
          readonly: false,
        });
        openSyncCreatePanel(accessTargetPath);
      } else {
        void handleCreateAndSync(provider);
      }
    },
    [accessTargetPath, handleCreateAndSync, openSyncCreatePanel, openSyncSetting],
  );

  const createMenuActions = useMemo<DataCreateMenuActions>(() => {
    const getTargetFolderPath = () =>
      createInFolderId === undefined ? currentFolderId : createInFolderId;

    // Pick a name that doesn't collide with an existing entry at the
    // target folder. We compare against the CANONICAL filename
    // (basename + extension) — without the extension, an existing
    // `Untitled Note.md` would never be detected against the bare
    // baseName `Untitled Note`, so every click would re-write the
    // same path with empty content and the version engine would correctly recognise
    // it as a no-op (same blob hash → same scope hash → no commit
    // delta). That looks identical to a broken save: the file
    // already exists on the server but nothing visible has changed,
    // so the user keeps clicking and getting nothing.
    //
    // Convention: `Base`, `Base (2)`, `Base (3)`, … — same pattern
    // every native file manager uses. Returns the canonical filename
    // including extension so the caller can use it directly as the
    // path segment without ever appending the extension a second
    // time on the backend.
    const pickAvailableName = async (
      targetFolderPath: string | null,
      baseName: string,
      extension: string = '',
    ): Promise<string> => {
      const parentPath = targetFolderPath ?? '';
      let siblings = readCachedDirectoryNodes(cache, projectId, parentPath);

      if (!siblings) {
        try {
          const listing = await listDir(projectId, parentPath);
          siblings = listing.nodes;
        } catch (err) {
          // If we can't reach the tree endpoint, fall back to the
          // base name and let the create call surface its own error.
          // Worse than the dedup-aware path but no worse than the
          // pre-fix behaviour.
          console.warn('treeList lookup failed, skipping dedup:', err);
          return `${baseName}${extension}`;
        }
      }
      const existing = new Set(siblings.map((e) => e.name));
      const canonical = `${baseName}${extension}`;
      if (!existing.has(canonical)) return canonical;
      for (let n = 2; n < 1000; n++) {
        const candidate = `${baseName} (${n})${extension}`;
        if (!existing.has(candidate)) return candidate;
      }
      // Outrageously unlikely fallback — append a timestamp so the
      // user gets a unique name rather than an infinite hang.
      return `${baseName} (${Date.now()})${extension}`;
    };

    return {
      onClose: closeCreateMenu,
      onCreateFolder: async () => {
        const targetFolderPath = getTargetFolderPath();
        const folderName = await pickAvailableName(
          targetFolderPath,
          'New Folder',
        );
        const folderPath = targetFolderPath
          ? `${targetFolderPath}/${folderName}`
          : folderName;
        const parentPath = targetFolderPath ?? '';

        addPendingCreatingPath(projectId, folderPath);
        optimisticInsertDirectoryNode(
          projectId,
          parentPath,
          buildOptimisticNode({
            projectId, path: folderPath, name: folderName, type: 'folder',
          }),
        );
        optimisticSeedEmptyDirectory(projectId, folderPath);
        if (targetFolderPath) ensureExpanded(projectId, targetFolderPath);

        let keepPendingForRecovery = false;
        try {
          showToast?.(`Creating "${folderName}"...`, 'loading', null);
          await mkdir(projectId, folderPath);
          await refreshFolderNodes(projectId, parentPath, folderPath);
          void refreshProjectHistory(projectId);
          showToast?.(`Created "${folderName}"`);
        } catch (err) {
          console.error('Failed to create folder:', err);
          if (isAmbiguousWriteFailure(err)) {
            keepPendingForRecovery = true;
            showToast?.(`Checking "${folderName}"...`, 'loading', 5000);
            recoverAmbiguousFolderCreate({
              removePendingCreatingPath,
              projectId,
              parentPath,
              folderPath,
              folderName,
              showToast,
            });
            return;
          }
          optimisticRemoveDirectoryNode(projectId, parentPath, folderPath);
          await refreshFolderNodes(projectId, parentPath);
          showToast?.(`Failed to create "${folderName}"`, 'error');
        } finally {
          // For definitive failures and successful creates, clear the pending
          // state here. Ambiguous network failures let the recovery verifier
          // clear pending only after it observes a canonical server result.
          if (!keepPendingForRecovery) {
            removePendingCreatingPath(projectId, folderPath);
          }
        }
      },
      onCreateBlankJson: async () => {
        const targetFolderPath = getTargetFolderPath();
        const fileName = await pickAvailableName(
          targetFolderPath,
          'Untitled',
          '.json',
        );
        const filePath = targetFolderPath
          ? `${targetFolderPath}/${fileName}`
          : fileName;
        const parentPath = targetFolderPath ?? '';

        addPendingCreatingNode(projectId, filePath, { label: 'Creating JSON' });
        optimisticInsertDirectoryNode(
          projectId,
          parentPath,
          buildOptimisticNode({
            projectId, path: filePath, name: fileName, type: 'json',
          }),
        );
        if (targetFolderPath) ensureExpanded(projectId, targetFolderPath);
        highlightCreatedNode(filePath);

        try {
          showToast?.(`Creating "${fileName}"...`, 'loading', null);
          await writeFile(projectId, filePath, {}, 'json');
          navigateTo(filePath.split('/').filter(Boolean), 'json');
          await refreshFolderNodes(projectId, parentPath);
          void refreshProjectHistory(projectId);
          showToast?.(`Created "${fileName}"`);
        } catch (err) {
          console.error('Failed to create JSON:', err);
          optimisticRemoveDirectoryNode(projectId, parentPath, filePath);
          await refreshFolderNodes(projectId, parentPath);
          showToast?.(`Failed to create "${fileName}"`, 'error');
        } finally {
          removePendingCreatingPath(projectId, filePath);
        }
      },
      onCreateBlankMarkdown: async () => {
        const targetFolderPath = getTargetFolderPath();
        const fileName = await pickAvailableName(
          targetFolderPath,
          'Untitled Note',
          '.md',
        );
        const filePath = targetFolderPath
          ? `${targetFolderPath}/${fileName}`
          : fileName;
        const parentPath = targetFolderPath ?? '';

        addPendingCreatingNode(projectId, filePath, { label: 'Creating Markdown' });
        optimisticInsertDirectoryNode(
          projectId,
          parentPath,
          buildOptimisticNode({
            projectId, path: filePath, name: fileName, type: 'markdown',
          }),
        );
        if (targetFolderPath) ensureExpanded(projectId, targetFolderPath);
        highlightCreatedNode(filePath);

        try {
          showToast?.(`Creating "${fileName}"...`, 'loading', null);
          await writeFile(projectId, filePath, '', 'markdown');
          navigateTo(filePath.split('/').filter(Boolean), 'markdown');
          await refreshFolderNodes(projectId, parentPath);
          void refreshProjectHistory(projectId);
          showToast?.(`Created "${fileName}"`);
        } catch (err) {
          console.error('Failed to create markdown:', err);
          optimisticRemoveDirectoryNode(projectId, parentPath, filePath);
          await refreshFolderNodes(projectId, parentPath);
          showToast?.(`Failed to create "${fileName}"`, 'error');
        } finally {
          removePendingCreatingPath(projectId, filePath);
        }
      },
      onImportFromFiles: () => {
        const targetFolderPath = getTargetFolderPath();
        const target = {
          path: targetFolderPath ?? null,
          name: folderDisplayName(targetFolderPath ?? null),
        };
        if (openFileImportDialogForTarget) {
          openFileImportDialogForTarget(target);
          return;
        }
        if (openFilePickerForTarget) {
          openFilePickerForTarget(target);
          return;
        }
        setDefaultStartOption('documents');
        setCreateTableOpen(true);
      },
      // From `+` menu (Upload → URL): open the createTable dialog
      // for inline URL import.
      // From plug menu (New Access → Web Page): same provider id
      // ("url" on the backend), but go through handleAccessSelect
      // so the panel lands on the URL connector's config view with
      // the user's plug-clicked folder pre-bound as target.  Branch
      // on accessTargetPath to pick which flow to run.
      onImportFromUrl: () => {
        if (accessTargetPath !== null) {
          handleAccessSelect('url');
        } else {
          setDefaultStartOption('url');
          setCreateTableOpen(true);
        }
      },
      // From `+` menu (New Access → More Sources…): generic
      // exploration entry, opens the panel on the picker view.
      // From plug menu: this entry is suppressed at the menu level
      // (see CreateMenu's accessOnly branch) — there's no
      // sensible "I don't know what kind of access I want" path
      // when the user already committed by clicking a specific
      // folder's `Expose as...` command. Keep the callback defined for the `+` menu
      // path; the no-op-on-prefilled-target branch is just safety.
      onImportFromSaas: () => {
        if (accessTargetPath !== null) {
          return;
        }
        openSyncSetting('_generic');
        openSyncCreatePanel();
      },
      // Concrete provider entries in the Data page are durable Connect
      // flows. GitHub here means project/repository branch binding, never
      // the one-time URL snapshot import shown on the empty-workspace screen.
      // handleAccessSelect decides whether to prefill the scoped folder or
      // mint a new sync node first.
      onImportNotion: () => handleAccessSelect('notion'),
      onConnectGitHub: () => handleAccessSelect('github'),
      onImportGmail: () => handleAccessSelect('gmail'),
      onImportDocs: () => handleAccessSelect('docs'),
      onImportCalendar: () => handleAccessSelect('calendar'),
      onImportSheets: () => handleAccessSelect('sheets'),
      onConnectSupabase: () => handleAccessSelect('supabase'),
      onImportSearchConsole: () => handleAccessSelect('google_search_console'),
      onCreateAgent: () => handleAccessSelect('chat'),
      onCreateMcp: () => handleAccessSelect('mcp'),
      onCreateSandbox: () => handleAccessSelect('sandbox'),
      // SSH Terminal (Remote Dev) lives on the Access page as a scope-level
      // card, not a connector panel — route there, preselecting the scope that
      // matches the folder the user acted on (?path → matched in access page).
      onCreateSshTerminal: () => {
        closeCreateMenu();
        const path = accessTargetPath ?? '';
        openPanel({ type: 'access_list', view: 'detail', nodeId: path });
      },
    };
  }, [
    accessTargetPath,
    cache,
    closeCreateMenu,
    createInFolderId,
    currentFolderId,
    handleAccessSelect,
    highlightCreatedNode,
    navigateTo,
    openSyncCreatePanel,
    openSyncSetting,
    openFilePickerForTarget,
    openFileImportDialogForTarget,
    projectId,
    openPanel,
    showToast,
  ]);

  const closeCreateTable = useCallback(() => {
    setCreateTableOpen(false);
    setDefaultStartOption('documents');
  }, []);

  return {
    createTableOpen,
    defaultStartOption,
    createMenuOpen,
    createMenuOpenForId,
    createMenuOpenAction,
    createMenuPosition,
    createMenuAccessOnly,
    createMenuRef,
    createMenuActions,
    highlightNodeId,
    handleCreateClick,
    handleMillerCreateClick,
    handleAccessMenuClick,
    closeCreateTable,
  };
}
