import { Link2, MoreVertical, Plus } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import type { DataCapabilities, DataNode, DataPort, FileContent, Workspace } from "../core/types";
import { defaultDataCapabilities } from "../core/types";
import { shouldReadEditorContent } from "../editor/viewerRegistry";
import {
  createMarkdownLinkGraph,
  type MarkdownLinkGraphDocument,
} from "../editor/markdown/links/markdownLinkGraph";
import { ExplorerTree } from "./ExplorerTree";
import { FilePreview, type FilePreviewProps } from "./FilePreview";
import { ProjectsHeader } from "./ProjectsHeader";
import type { EditorSaveMode } from "../editor/PuppyoneEditorHost";
import type { MarkdownHtmlTrustMode } from "../editor/viewerTypes";
import { getAiEditFileForPath } from "../editor/ai-edits/diff";
import type { AiEditRequest } from "../editor/ai-edits/types";
import type { FileIconThemeId } from "../file/fileIcons";

export type DataWorkspaceState = {
  tree: DataNode[];
  activePath: string | null;
  activeNode: DataNode | null;
  currentFolderPath: string | null;
  selectedFile: DataNode | null;
  loadingPath: string | null;
  loadError: string | null;
  rootLoading: boolean;
  fileContent: FileContent | null;
  fileLoading: boolean;
  fileError: string | null;
  fileUrl: string | null;
  fileUrlLoading: boolean;
  fileUrlError: string | null;
};

type CommittedPreviewDocument = {
  node: DataNode;
  fileContent: FileContent | null;
  fileUrl: string | null;
  fileUrlLoading: boolean;
  fileUrlError: string | null;
  fileError: string | null;
};

export type DataWorkspaceSlot = ReactNode | ((state: DataWorkspaceState) => ReactNode);
export type DataWorkspaceFolderSlot = ReactNode | ((state: DataWorkspaceState, folder: DataNode) => ReactNode);
export type DataWorkspaceNodeSlot = ReactNode | ((state: DataWorkspaceState, node: DataNode) => ReactNode);

export type DataWorkspaceProps = {
  workspace: Workspace;
  dataPort: DataPort;
  capabilities?: DataCapabilities;
  activePath?: string | null;
  defaultActivePath?: string | null;
  showHeader?: boolean;
  showExplorerToolbar?: boolean;
  headerSlot?: DataWorkspaceSlot;
  headerActionSlot?: DataWorkspaceSlot;
  explorerToolbarSlot?: DataWorkspaceSlot;
  explorerSlot?: DataWorkspaceSlot;
  explorerFooterSlot?: DataWorkspaceSlot;
  collapsedExplorerSlot?: DataWorkspaceSlot;
  explorerRootActionSlot?: DataWorkspaceSlot;
  explorerFolderActionSlot?: DataWorkspaceFolderSlot;
  explorerNodeActionSlot?: DataWorkspaceNodeSlot;
  resizableExplorer?: boolean;
  explorerCollapsed?: boolean;
  explorerWidth?: number;
  defaultExplorerWidth?: number;
  minExplorerWidth?: number;
  maxExplorerWidth?: number;
  collapsedExplorerWidth?: number;
  mainSlot?: DataWorkspaceSlot;
  emptySlot?: ReactNode;
  showPreviewHeader?: boolean;
  hidePreviewSourceView?: boolean;
  fileIconTheme?: FileIconThemeId;
  editorSaveMode?: EditorSaveMode;
  htmlTrustMode?: MarkdownHtmlTrustMode;
  previewActionSlot?: FilePreviewProps["actionSlot"];
  renderPreviewBody?: FilePreviewProps["renderBody"];
  previewAccessorySlot?: DataWorkspaceSlot;
  aiEditRequest?: AiEditRequest | null;
  refreshKey?: unknown;
  onExplorerWidthChange?: (width: number) => void;
  onExplorerCollapsedChange?: (collapsed: boolean) => void;
  onActivePathChange?: (path: string | null, node: DataNode | null) => void;
  onOpenExternalUrl?: (href: string) => void | Promise<void>;
  onCreate?: (folderPath: string | null) => void;
  onMore?: (state: DataWorkspaceState) => void;
  onAccess?: (folderPath: string | null) => void;
  labels?: Partial<{
    root: string;
    loadingWorkspace: string;
  }>;
};

const ROOT_FOLDER_KEY = "__puppyone_workspace_root__";
const DEFAULT_EXPLORER_WIDTH = 320;
const MIN_EXPLORER_WIDTH = 240;
const MAX_EXPLORER_WIDTH = 520;
const COLLAPSED_EXPLORER_WIDTH = 47;
const MARKDOWN_LINK_INDEX_MAX_FILES = 250;

export function DataWorkspace({
  workspace,
  dataPort,
  capabilities,
  activePath,
  defaultActivePath = null,
  showHeader = true,
  showExplorerToolbar = true,
  headerSlot,
  headerActionSlot,
  explorerToolbarSlot,
  explorerSlot,
  explorerFooterSlot,
  collapsedExplorerSlot,
  explorerRootActionSlot,
  explorerFolderActionSlot,
  explorerNodeActionSlot,
  resizableExplorer = false,
  explorerCollapsed = false,
  explorerWidth,
  defaultExplorerWidth = DEFAULT_EXPLORER_WIDTH,
  minExplorerWidth = MIN_EXPLORER_WIDTH,
  maxExplorerWidth = MAX_EXPLORER_WIDTH,
  collapsedExplorerWidth = COLLAPSED_EXPLORER_WIDTH,
  mainSlot,
  emptySlot,
  showPreviewHeader = true,
  hidePreviewSourceView = false,
  fileIconTheme = "default",
  editorSaveMode = "manual",
  htmlTrustMode = "safe",
  previewActionSlot,
  renderPreviewBody,
  previewAccessorySlot,
  aiEditRequest = null,
  refreshKey,
  onExplorerWidthChange,
  onExplorerCollapsedChange,
  onActivePathChange,
  onOpenExternalUrl,
  onCreate,
  onMore,
  onAccess,
  labels,
}: DataWorkspaceProps) {
  const resolvedCapabilities = { ...defaultDataCapabilities, ...capabilities };
  const [tree, setTree] = useState<DataNode[]>([]);
  const [internalActivePath, setInternalActivePath] = useState<string | null>(defaultActivePath);
  const [rootLoaded, setRootLoaded] = useState(false);
  const [loadingFolderPaths, setLoadingFolderPaths] = useState<Set<string>>(() => new Set([ROOT_FOLDER_KEY]));
  const [expandedFolderPaths, setExpandedFolderPaths] = useState<Set<string>>(() => new Set(collectAncestorFolderPaths(defaultActivePath)));
  const [loadError, setLoadError] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<FileContent | null>(null);
  const [fileContentCache, setFileContentCache] = useState<Record<string, FileContent>>({});
  const [fileLoading, setFileLoading] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const [fileErrorPath, setFileErrorPath] = useState<string | null>(null);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [fileUrlPath, setFileUrlPath] = useState<string | null>(null);
  const [fileUrlLoading, setFileUrlLoading] = useState(false);
  const [fileUrlError, setFileUrlError] = useState<string | null>(null);
  const [markdownLinkIndexing, setMarkdownLinkIndexing] = useState(false);
  const [committedPreviewDocument, setCommittedPreviewDocument] = useState<CommittedPreviewDocument | null>(null);
  const lastRefreshKeyRef = useRef(refreshKey);
  const loadGenerationRef = useRef(0);
  const [internalExplorerWidth, setInternalExplorerWidth] = useState(() => (
    clampNumber(defaultExplorerWidth, minExplorerWidth, maxExplorerWidth)
  ));
  const resolvedActivePath = activePath !== undefined ? activePath : internalActivePath;
  const expandedExplorerWidth = clampNumber(
    explorerWidth ?? internalExplorerWidth,
    minExplorerWidth,
    maxExplorerWidth,
  );
  const resolvedExplorerWidth = clampNumber(
    explorerCollapsed ? collapsedExplorerWidth : expandedExplorerWidth,
    collapsedExplorerWidth,
    maxExplorerWidth,
  );

  const setExplorerWidth = useCallback(
    (nextWidth: number) => {
      const clampedWidth = clampNumber(nextWidth, minExplorerWidth, maxExplorerWidth);
      if (explorerWidth === undefined) setInternalExplorerWidth(clampedWidth);
      onExplorerWidthChange?.(clampedWidth);
    },
    [explorerWidth, maxExplorerWidth, minExplorerWidth, onExplorerWidthChange],
  );

  const setFolderLoading = useCallback((folderPath: string | null, loading: boolean) => {
    const loadingKey = getLoadingKey(folderPath);
    setLoadingFolderPaths((current) => {
      if (loading && current.has(loadingKey)) return current;
      if (!loading && !current.has(loadingKey)) return current;
      const next = new Set(current);
      if (loading) next.add(loadingKey);
      else next.delete(loadingKey);
      return next;
    });
  }, []);

  const isFolderLoaded = useCallback(
    (folderPath: string | null) => (
      folderPath ? hasLoadedFolder(tree, folderPath) : rootLoaded
    ),
    [rootLoaded, tree],
  );

  const loadFolder = useCallback(
    async (folderPath: string | null, force = false) => {
      if (!force && isFolderLoaded(folderPath)) return true;

      const requestGeneration = loadGenerationRef.current;
      setFolderLoading(folderPath, true);
      setLoadError(null);

      try {
        const children = await dataPort.listChildren(folderPath);
        if (requestGeneration !== loadGenerationRef.current) return false;
        setTree((current) => attachFolderChildren(current, folderPath, children));
        if (!folderPath) setRootLoaded(true);
        return true;
      } catch (error) {
        if (requestGeneration !== loadGenerationRef.current) return false;
        setLoadError(error instanceof Error ? error.message : String(error));
        return false;
      } finally {
        if (requestGeneration === loadGenerationRef.current) {
          setFolderLoading(folderPath, false);
        }
      }
    },
    [dataPort, isFolderLoaded, setFolderLoading],
  );

  useEffect(() => {
    loadGenerationRef.current += 1;
    setInternalActivePath(defaultActivePath);
    setTree([]);
    setRootLoaded(false);
    setExpandedFolderPaths(new Set(collectAncestorFolderPaths(defaultActivePath)));
    setLoadingFolderPaths(new Set([ROOT_FOLDER_KEY]));
    setLoadError(null);
    setFileContent(null);
    setFileContentCache({});
    setFileError(null);
    setFileErrorPath(null);
    setFileLoading(false);
    setFileUrl(null);
    setFileUrlPath(null);
    setFileUrlError(null);
    setFileUrlLoading(false);
    setMarkdownLinkIndexing(false);
    setCommittedPreviewDocument(null);
  }, [workspace.path, dataPort, defaultActivePath]);

  useEffect(() => {
    if (explorerWidth !== undefined) return;
    setInternalExplorerWidth(clampNumber(defaultExplorerWidth, minExplorerWidth, maxExplorerWidth));
  }, [defaultExplorerWidth, explorerWidth, maxExplorerWidth, minExplorerWidth]);

  useEffect(() => {
    let cancelled = false;
    const requestGeneration = loadGenerationRef.current;

    setFolderLoading(null, true);
    setLoadError(null);
    dataPort.listChildren(null)
      .then((children) => {
        if (cancelled || requestGeneration !== loadGenerationRef.current) return;
        setTree(children);
        setRootLoaded(true);
      })
      .catch((error) => {
        if (!cancelled && requestGeneration === loadGenerationRef.current) {
          setLoadError(error instanceof Error ? error.message : String(error));
        }
      })
      .finally(() => {
        if (!cancelled && requestGeneration === loadGenerationRef.current) {
          setFolderLoading(null, false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [workspace.path, dataPort, setFolderLoading]);

  useEffect(() => {
    if (refreshKey === undefined || Object.is(lastRefreshKeyRef.current, refreshKey)) {
      return undefined;
    }

    lastRefreshKeyRef.current = refreshKey;
    const loadedFolderPaths = collectLoadedFolderPaths(tree);
    let cancelled = false;
    const requestGeneration = loadGenerationRef.current;

    setFolderLoading(null, true);
    setLoadError(null);

    dataPort.listChildren(null)
      .then(async (rootChildren) => {
        const folderResults = await Promise.all(
          loadedFolderPaths.map(async (folderPath) => ({
            folderPath,
            children: await dataPort.listChildren(folderPath).catch(() => null),
          })),
        );

        if (cancelled) return;
        if (requestGeneration !== loadGenerationRef.current) return;

        let nextTree = rootChildren;
        for (const result of folderResults) {
          if (result.children) {
            nextTree = attachFolderChildren(nextTree, result.folderPath, result.children);
          }
        }
        setTree(nextTree);
        setRootLoaded(true);
      })
      .catch((error) => {
        if (!cancelled && requestGeneration === loadGenerationRef.current) {
          setLoadError(error instanceof Error ? error.message : String(error));
        }
      })
      .finally(() => {
        if (!cancelled && requestGeneration === loadGenerationRef.current) {
          setFolderLoading(null, false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [dataPort, refreshKey, setFolderLoading, tree]);

  const activeNode = useMemo(() => findDataNode(tree, resolvedActivePath), [resolvedActivePath, tree]);
  const currentFolderPath = activeNode?.type === "folder" ? activeNode.path : getParentPath(resolvedActivePath);
  const selectedFile = activeNode?.type !== "folder" ? activeNode : null;
  const selectedFileNeedsFullContent = Boolean(selectedFile && dataPort.readFile && shouldReadEditorContent(selectedFile));
  const cachedSelectedFileContent = selectedFile ? fileContentCache[selectedFile.path] ?? null : null;
  const selectedFileContent = fileContent?.path === selectedFile?.path ? fileContent : cachedSelectedFileContent;
  const selectedFileError = fileErrorPath === selectedFile?.path ? fileError : null;
  const selectedFileContentPending = Boolean(
    selectedFileNeedsFullContent && selectedFile && !selectedFileContent && !selectedFileError,
  );
  const selectedFileUrl = fileUrlPath === selectedFile?.path ? fileUrl : null;
  const selectedFileUrlLoading = fileUrlPath === selectedFile?.path ? fileUrlLoading : false;
  const selectedFileUrlError = fileUrlPath === selectedFile?.path ? fileUrlError : null;
  const selectedPreviewDocument = useMemo<CommittedPreviewDocument | null>(() => (
    selectedFile
      ? {
          node: selectedFile,
          fileContent: selectedFileContent,
          fileUrl: selectedFileUrl,
          fileUrlLoading: selectedFileUrlLoading,
          fileUrlError: selectedFileUrlError,
          fileError: selectedFileError,
        }
      : null
  ), [selectedFile, selectedFileContent, selectedFileError, selectedFileUrl, selectedFileUrlError, selectedFileUrlLoading]);
  const renderedPreviewDocument = selectedFileContentPending && committedPreviewDocument
    ? committedPreviewDocument
    : selectedPreviewDocument;
  const renderedPreviewIsSelectedFile = renderedPreviewDocument?.node.path === selectedFile?.path;
  const renderedPreviewLoading = renderedPreviewIsSelectedFile
    ? fileLoading || selectedFileContentPending
    : selectedFileContentPending;
  const renderedPreviewError = renderedPreviewIsSelectedFile ? selectedFileError : null;
  const renderedPreviewUrlLoading = renderedPreviewIsSelectedFile
    ? selectedFileUrlLoading
    : renderedPreviewDocument?.fileUrlLoading ?? false;
  const renderedPreviewUrlError = renderedPreviewIsSelectedFile
    ? selectedFileUrlError
    : renderedPreviewDocument?.fileUrlError ?? null;
  const renderedPreviewAiEditFile = getAiEditFileForPath(aiEditRequest, renderedPreviewDocument?.node.path);
  const pathSegments = buildBreadcrumb(workspace.name, currentFolderPath, selectedFile?.name)
    .map((label) => ({ label }));
  const loadingPath = getFirstSetValue(loadingFolderPaths);
  const rootLoading = loadingFolderPaths.has(ROOT_FOLDER_KEY);
  const filesExplorerActive = !explorerSlot;
  const activateNode = useCallback(
    (node: DataNode | null) => {
      const nextPath = node?.path ?? null;
      if (activePath === undefined) setInternalActivePath(nextPath);
      onActivePathChange?.(nextPath, node);
      if (!node) {
        void loadFolder(null);
      }
    },
    [activePath, loadFolder, onActivePathChange],
  );
  const loadLinkedPathNode = useCallback(
    async (path: string): Promise<DataNode | null> => {
      const normalizedPath = normalizeDataPath(path);
      if (!normalizedPath) return null;

      const requestGeneration = loadGenerationRef.current;
      let workingTree = tree;

      const loadChildren = async (folderPath: string | null): Promise<DataNode[] | null> => {
        if (requestGeneration !== loadGenerationRef.current) return null;

        setFolderLoading(folderPath, true);
        setLoadError(null);

        try {
          const children = await dataPort.listChildren(folderPath);
          if (requestGeneration !== loadGenerationRef.current) return null;

          workingTree = folderPath ? attachFolderChildren(workingTree, folderPath, children) : children;
          setTree((current) => (folderPath ? attachFolderChildren(current, folderPath, children) : children));
          if (!folderPath) setRootLoaded(true);
          return children;
        } catch (error) {
          if (requestGeneration === loadGenerationRef.current) {
            setLoadError(error instanceof Error ? error.message : String(error));
          }
          return null;
        } finally {
          if (requestGeneration === loadGenerationRef.current) {
            setFolderLoading(folderPath, false);
          }
        }
      };

      if (!rootLoaded) {
        const rootChildren = await loadChildren(null);
        if (!rootChildren) return null;
      }

      const ancestorPaths = collectAncestorFolderPaths(normalizedPath);
      for (const folderPath of ancestorPaths) {
        let folder = findDataNode(workingTree, folderPath);
        if (!folder || folder.type !== "folder") return null;

        if (!Array.isArray(folder.children)) {
          const children = await loadChildren(folderPath);
          if (!children) return null;
          folder = findDataNode(workingTree, folderPath);
        }

        if (!folder || folder.type !== "folder") return null;
      }

      const node = findDataNode(workingTree, normalizedPath);
      if (node) {
        setExpandedFolderPaths((current) => addSetValues(current, ancestorPaths));
      }
      return node;
    },
    [dataPort, rootLoaded, setFolderLoading, tree],
  );
  const openMarkdownLinkCandidates = useCallback(
    async (paths: readonly string[]) => {
      const normalizedPaths = paths
        .map(normalizeDataPath)
        .filter((path, index, allPaths): path is string => Boolean(path) && allPaths.indexOf(path) === index);

      for (const path of normalizedPaths) {
        const node = findDataNode(tree, path) ?? await loadLinkedPathNode(path);
        if (!node) continue;

        if (activePath === undefined) setInternalActivePath(node.path);
        onActivePathChange?.(node.path, node);
        if (node.type === "folder") {
          void loadFolder(node.path);
        }
        return;
      }
    },
    [activePath, loadFolder, loadLinkedPathNode, onActivePathChange, tree],
  );
  const markdownLinkDocuments = useMemo(
    () => buildMarkdownLinkDocuments(tree, fileContentCache),
    [fileContentCache, tree],
  );
  const markdownLinkGraph = useMemo(
    () => createMarkdownLinkGraph(markdownLinkDocuments, {
      isIndexing: markdownLinkIndexing,
      onOpenPath: (path) => {
        void openMarkdownLinkCandidates([path]);
      },
      onOpenCandidatePaths: (paths) => {
        void openMarkdownLinkCandidates(paths);
      },
      onOpenExternalUrl: (href) => {
        void onOpenExternalUrl?.(href);
      },
    }),
    [markdownLinkDocuments, markdownLinkIndexing, onOpenExternalUrl, openMarkdownLinkCandidates],
  );
  const workspaceState: DataWorkspaceState = {
    tree,
    activePath: resolvedActivePath,
    activeNode,
    currentFolderPath,
    selectedFile,
    loadingPath,
    loadError,
    rootLoading,
    fileContent: selectedFileContent,
    fileLoading: fileLoading || selectedFileContentPending,
    fileError: selectedFileError,
    fileUrl: selectedFileUrl,
    fileUrlLoading: selectedFileUrlLoading,
    fileUrlError: selectedFileUrlError,
  };
  const previewAccessory = renderWorkspaceSlot(previewAccessorySlot, workspaceState);

  useEffect(() => {
    const ancestorPaths = collectAncestorFolderPaths(resolvedActivePath);
    if (ancestorPaths.length === 0) return;

    setExpandedFolderPaths((current) => addSetValues(current, ancestorPaths));
  }, [resolvedActivePath]);

  useEffect(() => {
    if (!selectedPreviewDocument) {
      setCommittedPreviewDocument(null);
      return;
    }

    if (selectedFileContentPending) return;
    setCommittedPreviewDocument(selectedPreviewDocument);
  }, [selectedPreviewDocument, selectedFileContentPending]);

  useEffect(() => {
    if (!selectedFile) {
      setFileContent(null);
      setFileError(null);
      setFileErrorPath(null);
      setFileLoading(false);
      return undefined;
    }

    if (!dataPort.readFile) {
      setFileContent(null);
      setFileError(null);
      setFileErrorPath(null);
      setFileLoading(false);
      return undefined;
    }

    if (!shouldReadEditorContent(selectedFile)) {
      setFileContent(null);
      setFileError(null);
      setFileErrorPath(null);
      setFileLoading(false);
      return undefined;
    }

    let cancelled = false;
    setFileContent(null);
    setFileLoading(true);
    setFileError(null);
    setFileErrorPath(null);
    dataPort.readFile(selectedFile.path)
      .then((content) => {
        if (!cancelled) {
          setFileContent(content);
          setFileContentCache((current) => ({
            ...current,
            [content.path]: content,
          }));
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setFileContent(null);
          setFileErrorPath(selectedFile.path);
          setFileError(error instanceof Error ? error.message : String(error));
        }
      })
      .finally(() => {
        if (!cancelled) setFileLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [dataPort, refreshKey, selectedFile?.path]);

  useEffect(() => {
    if (!dataPort.readFile) {
      setMarkdownLinkIndexing(false);
      return undefined;
    }

    const candidates = collectMarkdownLinkIndexCandidates(tree, fileContentCache)
      .slice(0, MARKDOWN_LINK_INDEX_MAX_FILES);
    if (candidates.length === 0) {
      setMarkdownLinkIndexing(false);
      return undefined;
    }

    let cancelled = false;
    setMarkdownLinkIndexing(true);

    readMarkdownLinkIndexFiles(
      candidates,
      (path) => dataPort.readFile?.(path) ?? Promise.reject(new Error("readFile is unavailable")),
    )
      .then((contents) => {
        if (cancelled || contents.length === 0) return;
        setFileContentCache((current) => {
          const next = { ...current };
          for (const content of contents) {
            if (typeof content.content === "string") next[content.path] = content;
          }
          return next;
        });
      })
      .finally(() => {
        if (!cancelled) setMarkdownLinkIndexing(false);
      });

    return () => {
      cancelled = true;
    };
  }, [dataPort, fileContentCache, refreshKey, tree]);

  useEffect(() => {
    if (!selectedFile || !dataPort.getFileUrl) {
      setFileUrl(null);
      setFileUrlPath(null);
      setFileUrlError(null);
      setFileUrlLoading(false);
      return undefined;
    }

    let cancelled = false;
    setFileUrl(null);
    setFileUrlPath(selectedFile.path);
    setFileUrlLoading(true);
    setFileUrlError(null);

    Promise.resolve(dataPort.getFileUrl(selectedFile.path))
      .then((url) => {
        if (!cancelled) setFileUrl(url);
      })
      .catch((error) => {
        if (!cancelled) {
          setFileUrl(null);
          setFileUrlPath(selectedFile.path);
          setFileUrlError(error instanceof Error ? error.message : String(error));
        }
      })
      .finally(() => {
        if (!cancelled) setFileUrlLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [dataPort, selectedFile?.path]);

  const toggleFolder = useCallback(
    (node: DataNode, expanded: boolean) => {
      if (!expanded) {
        setExpandedFolderPaths((current) => deleteSetValue(current, node.path));
        return;
      }

      if (Array.isArray(node.children)) {
        setExpandedFolderPaths((current) => addSetValue(current, node.path));
        return;
      }

      if (loadingFolderPaths.has(node.path)) return;

      void loadFolder(node.path).then((loaded) => {
        if (!loaded) return;
        setExpandedFolderPaths((current) => addSetValue(current, node.path));
      });
    },
    [loadFolder, loadingFolderPaths],
  );

  const saveFileContent = async (node: DataNode, content: string) => {
    if (!dataPort.writeFile) return;

    await dataPort.writeFile(node.path, content);

    const existingContent = fileContent?.path === node.path
      ? fileContent
      : fileContentCache[node.path] ?? null;
    const nextContent: FileContent = existingContent
      ? { ...existingContent, content }
      : {
          path: node.path,
          name: node.name,
          type: node.type,
          content,
        };

    setFileContent((current) => (
      current?.path === node.path || selectedFile?.path === node.path
        ? nextContent
        : current
    ));
    setFileContentCache((current) => ({
      ...current,
      [node.path]: nextContent,
    }));
    setCommittedPreviewDocument((current) => (
      current?.node.path === node.path
        ? {
            ...current,
            node: { ...current.node, content },
            fileContent: nextContent,
          }
        : current
    ));
    setTree((current) => updateNodeContent(current, node.path, content));
  };

  const moveNode = useCallback(
    async (node: DataNode, targetFolderPath: string | null) => {
      if (!resolvedCapabilities.move || !dataPort.moveNode) return;

      const previousPath = node.path;
      const nextPath = joinDataPath(targetFolderPath, node.name);
      const previousParentPath = getParentPath(previousPath);

      if (previousPath === nextPath || previousParentPath === targetFolderPath) return;

      setLoadError(null);

      try {
        await dataPort.moveNode(previousPath, nextPath);
      } catch (error) {
        setLoadError(error instanceof Error ? error.message : String(error));
        return;
      }

      setTree((current) => moveDataNode(current, previousPath, nextPath, targetFolderPath));
      setFileContentCache((current) => rebaseFileContentCache(current, previousPath, nextPath));
      setFileContent((current) => rebaseFileContent(current, previousPath, nextPath));
      setFileErrorPath((current) => rebaseMovedPath(current, previousPath, nextPath));
      setFileUrlPath((current) => rebaseMovedPath(current, previousPath, nextPath));
      setCommittedPreviewDocument((current) => rebaseCommittedPreviewDocument(current, previousPath, nextPath));

      const nextActivePath = rebaseMovedPath(resolvedActivePath, previousPath, nextPath);
      if (nextActivePath !== resolvedActivePath) {
        const nextActiveNode = activeNode ? rebaseDataNode(activeNode, previousPath, nextPath) : null;
        if (activePath === undefined) setInternalActivePath(nextActivePath);
        onActivePathChange?.(nextActivePath, nextActiveNode);
      }

      void loadFolder(previousParentPath, true);
      if (targetFolderPath !== previousParentPath) {
        void loadFolder(targetFolderPath, true);
      }
    },
    [
      activeNode,
      activePath,
      dataPort,
      loadFolder,
      onActivePathChange,
      resolvedActivePath,
      resolvedCapabilities.move,
    ],
  );

  const beginExplorerResize = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!resizableExplorer) return;

      event.preventDefault();
      const startX = event.clientX;
      const startWidth = explorerCollapsed ? minExplorerWidth : expandedExplorerWidth;

      if (explorerCollapsed) {
        onExplorerCollapsedChange?.(false);
      }

      const moveExplorerResize = (moveEvent: PointerEvent) => {
        setExplorerWidth(startWidth + moveEvent.clientX - startX);
      };

      const stopExplorerResize = () => {
        window.removeEventListener("pointermove", moveExplorerResize);
        window.removeEventListener("pointerup", stopExplorerResize);
        document.body.classList.remove("data-sidebar-resizing");
      };

      document.body.classList.add("data-sidebar-resizing");
      window.addEventListener("pointermove", moveExplorerResize);
      window.addEventListener("pointerup", stopExplorerResize);
    },
    [expandedExplorerWidth, explorerCollapsed, minExplorerWidth, onExplorerCollapsedChange, resizableExplorer, setExplorerWidth],
  );

  const nudgeExplorerWidth = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!resizableExplorer) return;

    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setExplorerWidth(resolvedExplorerWidth - (event.shiftKey ? 24 : 12));
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      setExplorerWidth(resolvedExplorerWidth + (event.shiftKey ? 24 : 12));
    } else if (event.key === "Home") {
      event.preventDefault();
      setExplorerWidth(minExplorerWidth);
    } else if (event.key === "End") {
      event.preventDefault();
      setExplorerWidth(maxExplorerWidth);
    }
  };

  const dataContentStyle = resizableExplorer
    ? ({ "--data-explorer-width": `${resolvedExplorerWidth}px` } as CSSProperties)
    : undefined;

  return (
    <section className="data-workspace">
      {showHeader && (
        headerSlot ? (
          renderWorkspaceSlot(headerSlot, workspaceState)
        ) : (
          <ProjectsHeader
            pathSegments={pathSegments}
            actionSlot={renderWorkspaceSlot(headerActionSlot, workspaceState)}
          />
        )
      )}

      <div
        className="data-content"
        data-explorer-collapsed={explorerCollapsed ? "true" : undefined}
        data-resizable-explorer={resizableExplorer ? "true" : undefined}
        style={dataContentStyle}
      >
        <aside className="explorer-column">
          {!explorerCollapsed && (
            <>
              {showExplorerToolbar && (
                explorerToolbarSlot ? (
                  renderWorkspaceSlot(explorerToolbarSlot, workspaceState)
                ) : (
                  <div className="desktop-explorer-toolbar">
                    <span>{labels?.root ?? "Root"}</span>
                    <div className="desktop-explorer-actions">
                      {resolvedCapabilities.create && onCreate && (
                        <button type="button" aria-label="Create" onClick={() => onCreate(currentFolderPath)}>
                          <Plus size={15} />
                        </button>
                      )}
                      {onMore && (
                        <button type="button" aria-label="More" onClick={() => onMore(workspaceState)}>
                          <MoreVertical size={15} />
                        </button>
                      )}
                      {resolvedCapabilities.accessPoints && onAccess && (
                        <button type="button" aria-label="Access" onClick={() => onAccess(currentFolderPath)}>
                          <Link2 size={15} />
                        </button>
                      )}
                    </div>
                  </div>
                )
              )}
              <div className="data-explorer-view-stack" data-view-mode={filesExplorerActive ? "files" : "custom"}>
                <div
                  className="data-explorer-view-frame"
                  data-view-mode="files"
                  data-active={filesExplorerActive ? "true" : "false"}
                  aria-hidden={filesExplorerActive ? undefined : true}
                >
                  <ExplorerTree
                    nodes={tree}
                    activePath={resolvedActivePath}
                    expandedPaths={expandedFolderPaths}
                    loadingPaths={loadingFolderPaths}
                    rootLoading={rootLoading}
                    rootError={loadError}
                    rootLabel={labels?.root ?? "Root"}
                    showRoot
                    loadingLabel={labels?.loadingWorkspace ?? "Loading workspace..."}
                    onToggleFolder={toggleFolder}
                    onSelectNode={activateNode}
                    fileIconTheme={fileIconTheme}
                    canMoveNodes={Boolean(resolvedCapabilities.move && dataPort.moveNode)}
                    onMoveNode={moveNode}
                    renderRootActions={explorerRootActionSlot ? () => renderWorkspaceSlot(explorerRootActionSlot, workspaceState) : undefined}
                    renderFolderActions={explorerFolderActionSlot ? (folder) => renderWorkspaceFolderSlot(explorerFolderActionSlot, workspaceState, folder) : undefined}
                    renderNodeActions={explorerNodeActionSlot ? (node) => renderWorkspaceNodeSlot(explorerNodeActionSlot, workspaceState, node) : undefined}
                  />
                </div>
                {explorerSlot && (
                  <div className="data-explorer-view-frame" data-view-mode="custom" data-active="true">
                    {renderWorkspaceSlot(explorerSlot, workspaceState)}
                  </div>
                )}
              </div>
            </>
          )}
          {explorerCollapsed && (
            <div className="data-explorer-collapsed-fill" aria-hidden="true" />
          )}
          {!explorerCollapsed && explorerFooterSlot && (
            <div className="data-explorer-footer">
              {renderWorkspaceSlot(explorerFooterSlot, workspaceState)}
            </div>
          )}
          {resizableExplorer && !explorerCollapsed && (
            <div
              className="data-explorer-resizer"
              role="separator"
              aria-label="Resize sidebar"
              aria-orientation="vertical"
              aria-valuemin={minExplorerWidth}
              aria-valuemax={maxExplorerWidth}
              aria-valuenow={resolvedExplorerWidth}
              tabIndex={0}
              onPointerDown={beginExplorerResize}
              onKeyDown={nudgeExplorerWidth}
            />
          )}
        </aside>

        {explorerCollapsed && collapsedExplorerSlot && (
          <div className="data-explorer-collapsed-slot">
            {renderWorkspaceSlot(collapsedExplorerSlot, workspaceState)}
          </div>
        )}

        <main className="browser-column desktop-editor-panel">
          <div className="data-main-view-frame" data-view-mode={mainSlot ? "custom" : "files"}>
            {mainSlot ? (
              renderWorkspaceSlot(mainSlot, workspaceState)
            ) : (
              <>
                {previewAccessory && (
                  <div className="data-preview-accessory">
                    {previewAccessory}
                  </div>
                )}
                <FilePreview
                  node={renderedPreviewDocument?.node ?? null}
                  fileContent={renderedPreviewDocument?.fileContent ?? null}
                  fileUrl={renderedPreviewDocument?.fileUrl ?? null}
                  fileUrlLoading={renderedPreviewUrlLoading}
                  fileUrlError={renderedPreviewUrlError}
                  loading={renderedPreviewLoading}
                  error={renderedPreviewError}
                  aiEditFile={renderedPreviewAiEditFile}
                  showHeader={showPreviewHeader}
                  hideSourceView={hidePreviewSourceView}
                  fileIconTheme={fileIconTheme}
                  editorSaveMode={editorSaveMode}
                  htmlTrustMode={htmlTrustMode}
                  markdownLinkGraph={markdownLinkGraph}
                  emptySlot={emptySlot}
                  actionSlot={previewActionSlot}
                  renderBody={renderPreviewBody}
                  onSaveContent={dataPort.writeFile && renderedPreviewDocument
                    ? (content) => saveFileContent(renderedPreviewDocument.node, content)
                    : undefined}
                />
              </>
            )}
          </div>
        </main>
      </div>
    </section>
  );
}

function clampNumber(value: number, min: number, max: number): number {
  return Math.min(Math.max(Math.round(value), min), max);
}

function getFirstSetValue<T>(values: ReadonlySet<T>): T | null {
  return values.values().next().value ?? null;
}

function addSetValue<T>(current: Set<T>, value: T): Set<T> {
  if (current.has(value)) return current;
  const next = new Set(current);
  next.add(value);
  return next;
}

function addSetValues<T>(current: Set<T>, values: readonly T[]): Set<T> {
  let next: Set<T> | null = null;
  for (const value of values) {
    if (current.has(value)) continue;
    next ??= new Set(current);
    next.add(value);
  }
  return next ?? current;
}

function deleteSetValue<T>(current: Set<T>, value: T): Set<T> {
  if (!current.has(value)) return current;
  const next = new Set(current);
  next.delete(value);
  return next;
}

function findDataNode(nodes: DataNode[], path: string | null): DataNode | null {
  if (!path) return null;

  for (const node of nodes) {
    if (node.path === path) return node;
    if (node.children) {
      const child = findDataNode(node.children, path);
      if (child) return child;
    }
  }

  return null;
}

function hasLoadedFolder(nodes: DataNode[], folderPath: string | null): boolean {
  if (!folderPath) return nodes.length > 0;
  const node = findDataNode(nodes, folderPath);
  return node?.type === "folder" && Array.isArray(node.children);
}

function collectLoadedFolderPaths(nodes: DataNode[]): string[] {
  const paths: string[] = [];

  for (const node of nodes) {
    if (node.type === "folder" && Array.isArray(node.children)) {
      paths.push(node.path);
      paths.push(...collectLoadedFolderPaths(node.children));
    }
  }

  return paths;
}

function attachFolderChildren(
  nodes: DataNode[],
  folderPath: string | null,
  children: DataNode[],
): DataNode[] {
  if (!folderPath) return children;

  return nodes.map((node) => {
    if (node.path === folderPath) {
      return { ...node, children };
    }
    if (node.children) {
      return {
        ...node,
        children: attachFolderChildren(node.children, folderPath, children),
      };
    }
    return node;
  });
}

function updateNodeContent(nodes: DataNode[], path: string, content: string): DataNode[] {
  return nodes.map((node) => {
    if (node.path === path) {
      return {
        ...node,
        content,
        preview: buildPreview(content),
      };
    }
    if (node.children) {
      return {
        ...node,
        children: updateNodeContent(node.children, path, content),
      };
    }
    return node;
  });
}

function moveDataNode(
  nodes: DataNode[],
  previousPath: string,
  nextPath: string,
  targetFolderPath: string | null,
): DataNode[] {
  const removed = removeDataNode(nodes, previousPath);
  if (!removed.node) return nodes;

  return insertDataNode(
    removed.nodes,
    targetFolderPath,
    rebaseDataNode(removed.node, previousPath, nextPath),
  );
}

function removeDataNode(nodes: DataNode[], path: string): { nodes: DataNode[]; node: DataNode | null } {
  let removedNode: DataNode | null = null;
  let changed = false;
  const nextNodes: DataNode[] = [];

  for (const node of nodes) {
    if (node.path === path) {
      removedNode = node;
      changed = true;
      continue;
    }

    if (node.children && !removedNode) {
      const result = removeDataNode(node.children, path);
      if (result.node) {
        removedNode = result.node;
        changed = true;
        nextNodes.push({ ...node, children: result.nodes });
        continue;
      }
    }

    nextNodes.push(node);
  }

  return {
    nodes: changed ? nextNodes : nodes,
    node: removedNode,
  };
}

function insertDataNode(nodes: DataNode[], targetFolderPath: string | null, movedNode: DataNode): DataNode[] {
  if (!targetFolderPath) {
    return sortDataNodes([...nodes, movedNode]);
  }

  let changed = false;
  const nextNodes = nodes.map((node) => {
    if (node.path === targetFolderPath && node.type === "folder") {
      if (!Array.isArray(node.children)) return node;
      changed = true;
      return {
        ...node,
        children: sortDataNodes([...node.children, movedNode]),
      };
    }

    if (node.children) {
      const nextChildren = insertDataNode(node.children, targetFolderPath, movedNode);
      if (nextChildren !== node.children) {
        changed = true;
        return { ...node, children: nextChildren };
      }
    }

    return node;
  });

  return changed ? nextNodes : nodes;
}

function sortDataNodes(nodes: DataNode[]): DataNode[] {
  return [...nodes].sort((left, right) => {
    const leftFolder = left.type === "folder";
    const rightFolder = right.type === "folder";
    if (leftFolder !== rightFolder) return leftFolder ? -1 : 1;
    return left.name.localeCompare(right.name, undefined, { sensitivity: "base" });
  });
}

function rebaseDataNode(node: DataNode, previousPath: string, nextPath: string): DataNode {
  const rebasedPath = rebaseMovedPath(node.path, previousPath, nextPath) ?? node.path;
  return {
    ...node,
    id: node.id === node.path ? rebasedPath : node.id,
    path: rebasedPath,
    children: node.children
      ? node.children.map((child) => rebaseDataNode(child, previousPath, nextPath))
      : node.children,
  };
}

function rebaseFileContentCache(
  cache: Record<string, FileContent>,
  previousPath: string,
  nextPath: string,
): Record<string, FileContent> {
  const nextCache: Record<string, FileContent> = {};

  for (const [path, content] of Object.entries(cache)) {
    const rebasedPath = rebaseMovedPath(path, previousPath, nextPath) ?? path;
    nextCache[rebasedPath] = rebaseFileContent(content, previousPath, nextPath) ?? content;
  }

  return nextCache;
}

function rebaseFileContent(
  content: FileContent | null,
  previousPath: string,
  nextPath: string,
): FileContent | null {
  const rebasedPath = rebaseMovedPath(content?.path ?? null, previousPath, nextPath);
  return content && rebasedPath ? { ...content, path: rebasedPath } : content;
}

function rebaseCommittedPreviewDocument(
  document: CommittedPreviewDocument | null,
  previousPath: string,
  nextPath: string,
): CommittedPreviewDocument | null {
  if (!document) return null;
  const rebasedNodePath = rebaseMovedPath(document.node.path, previousPath, nextPath);
  if (!rebasedNodePath) return document;

  return {
    ...document,
    node: rebaseDataNode(document.node, previousPath, nextPath),
    fileContent: rebaseFileContent(document.fileContent, previousPath, nextPath),
  };
}

function rebaseMovedPath(path: string | null, previousPath: string, nextPath: string): string | null {
  if (!path) return path;
  if (path === previousPath) return nextPath;
  if (path.startsWith(`${previousPath}/`)) return `${nextPath}${path.slice(previousPath.length)}`;
  return path;
}

function joinDataPath(folderPath: string | null, name: string): string {
  return folderPath ? `${folderPath}/${name}` : name;
}

function buildPreview(content: string): string | null {
  const preview = content
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 8)
    .join("\n");
  return preview || null;
}

function buildMarkdownLinkDocuments(
  nodes: DataNode[],
  fileContentCache: Record<string, FileContent>,
): MarkdownLinkGraphDocument[] {
  const documents: MarkdownLinkGraphDocument[] = [];

  for (const node of collectLinkableNodes(nodes)) {
    const cachedContent = fileContentCache[node.path];
    const content = isMarkdownNodeLike(node)
      ? cachedContent?.content ?? node.content ?? null
      : null;

    documents.push({
      path: node.path,
      name: node.name,
      content,
    });
  }

  return documents;
}

function collectMarkdownLinkIndexCandidates(
  nodes: DataNode[],
  fileContentCache: Record<string, FileContent>,
): string[] {
  const paths: string[] = [];

  for (const node of collectMarkdownNodes(nodes)) {
    if (typeof node.content === "string") continue;
    if (typeof fileContentCache[node.path]?.content === "string") continue;
    paths.push(node.path);
  }

  return paths;
}

async function readMarkdownLinkIndexFiles(
  paths: string[],
  readFile: NonNullable<DataPort["readFile"]>,
): Promise<FileContent[]> {
  const contents: FileContent[] = [];
  const batchSize = 6;

  for (let index = 0; index < paths.length; index += batchSize) {
    const batch = paths.slice(index, index + batchSize);
    const results = await Promise.all(
      batch.map(async (path) => {
        try {
          return await readFile(path);
        } catch {
          return null;
        }
      }),
    );

    for (const result of results) {
      if (result && isMarkdownNodeLike(result)) contents.push(result);
    }
  }

  return contents;
}

function collectMarkdownNodes(nodes: DataNode[]): DataNode[] {
  const markdownNodes: DataNode[] = [];

  for (const node of nodes) {
    if (isMarkdownNodeLike(node)) markdownNodes.push(node);
    if (node.children) markdownNodes.push(...collectMarkdownNodes(node.children));
  }

  return markdownNodes;
}

function collectLinkableNodes(nodes: DataNode[]): DataNode[] {
  const linkableNodes: DataNode[] = [];

  for (const node of nodes) {
    if (node.type !== "folder") linkableNodes.push(node);
    if (node.children) linkableNodes.push(...collectLinkableNodes(node.children));
  }

  return linkableNodes;
}

function isMarkdownNodeLike(node: Pick<DataNode, "name" | "path" | "type">): boolean {
  return node.type === "markdown" || /\.(?:md|markdown)$/i.test(node.name) || /\.(?:md|markdown)$/i.test(node.path);
}

function getParentPath(path: string | null): string | null {
  if (!path || !path.includes("/")) return null;
  return path.slice(0, path.lastIndexOf("/"));
}

function normalizeDataPath(path: string): string {
  const normalized = path.replace(/\\/g, "/").replace(/^\/+/, "").replace(/\/+/g, "/").trim();
  const parts: string[] = [];

  for (const part of normalized.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      parts.pop();
      continue;
    }
    parts.push(part);
  }

  return parts.join("/");
}

function collectAncestorFolderPaths(activePath: string | null): string[] {
  if (!activePath) return [];
  const parts = activePath.split("/").filter(Boolean);
  return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/"));
}

function getLoadingKey(folderPath: string | null): string {
  return folderPath ?? ROOT_FOLDER_KEY;
}

function buildBreadcrumb(workspaceName: string, folderPath: string | null, selectedFile?: string): string[] {
  const parts = [workspaceName];
  if (folderPath) parts.push(...folderPath.split("/"));
  if (selectedFile) parts.push(selectedFile);
  return parts;
}

function renderWorkspaceSlot(slot: DataWorkspaceSlot | undefined, state: DataWorkspaceState): ReactNode {
  if (!slot) return null;
  return typeof slot === "function" ? slot(state) : slot;
}

function renderWorkspaceFolderSlot(
  slot: DataWorkspaceFolderSlot | undefined,
  state: DataWorkspaceState,
  folder: DataNode,
): ReactNode {
  if (!slot) return null;
  return typeof slot === "function" ? slot(state, folder) : slot;
}

function renderWorkspaceNodeSlot(
  slot: DataWorkspaceNodeSlot | undefined,
  state: DataWorkspaceState,
  node: DataNode,
): ReactNode {
  if (!slot) return null;
  return typeof slot === "function" ? slot(state, node) : slot;
}
