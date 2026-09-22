import type { CSSProperties, DragEvent as ReactDragEvent, ReactNode } from "react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { DataNode } from "../core/types";
import { getMatchedExtension } from "../core/fileFormats";
import { FileGlyphIcon, type FileIconThemeId } from "../file/fileIcons";

export type ExplorerTreeProps = {
  nodes: DataNode[];
  activePath: string | null;
  loadingPath?: string | null;
  expandedPaths: ReadonlySet<string>;
  loadingPaths?: ReadonlySet<string>;
  rootLoading?: boolean;
  rootError?: string | null;
  rootLabel?: string;
  showRoot?: boolean;
  emptyLabel?: string;
  loadingLabel?: string;
  fileIconTheme?: FileIconThemeId;
  canMoveNodes?: boolean;
  onSelectNode: (node: DataNode | null) => void;
  onToggleFolder?: (node: DataNode, expanded: boolean) => void;
  onMoveNode?: (node: DataNode, targetFolderPath: string | null) => void | Promise<void>;
  renderRootActions?: () => ReactNode;
  renderFolderActions?: (node: DataNode) => ReactNode;
  renderNodeActions?: (node: DataNode) => ReactNode;
};

type TreeDropTarget = {
  rowPath: string | null;
  targetFolderPath: string | null;
  mode: "folder" | "parent";
  valid: boolean;
} | null;

type TreeDragController = {
  enabled: boolean;
  draggedNode: DataNode | null;
  dropTarget: TreeDropTarget;
  onNodeDragStart: (event: ReactDragEvent<HTMLButtonElement>, node: DataNode) => void;
  onNodeDragEnd: () => void;
  onRowDragOver: (
    event: ReactDragEvent<HTMLElement>,
    rowPath: string | null,
    targetFolderPath: string | null,
    mode: "folder" | "parent",
  ) => boolean;
  onRowDragLeave: (event: ReactDragEvent<HTMLElement>, rowPath: string | null) => void;
  onRowDrop: (event: ReactDragEvent<HTMLElement>, targetFolderPath: string | null) => void;
};

const EXPLORER_TREE_ROW_HEIGHT = 30;
const EXPLORER_TREE_ROW_GAP = 2;
const EXPLORER_TREE_INDENT = 24;
const EXPLORER_TREE_ROW_MARGIN_X = 6;
const EXPLORER_TREE_CONTENT_INSET = 8;
const EXPLORER_TREE_ROW_MARGIN_Y = EXPLORER_TREE_ROW_GAP / 2;
const EXPLORER_TREE_LINE_OVERDRAW = 2;
const EXPLORER_TREE_META_OFFSET = 14;
const ROOT_HEADER_TOP_PADDING = 5;
const SUBTREE_MOTION_MIN_MS = 170;
const SUBTREE_MOTION_MAX_MS = 340;
const SUBTREE_MOTION_PX_FACTOR = 0.28;
const SUBTREE_MOTION_EASE = "cubic-bezier(0.25, 0.1, 0.25, 1)";
export const EXPLORER_TREE_NODE_DRAG_TYPE = "application/x-puppyone-data-node-path";
const FOLDER_HOVER_EXPAND_MS = 620;
const FOLDER_PEER_DROP_ZONE_RATIO = 0.34;

export function ExplorerTree({
  nodes,
  activePath,
  loadingPath = null,
  expandedPaths,
  loadingPaths,
  rootLoading = false,
  rootError = null,
  rootLabel = "Root",
  showRoot = true,
  emptyLabel = "Empty folder",
  loadingLabel = "Loading...",
  fileIconTheme = "default",
  canMoveNodes = false,
  onSelectNode,
  onToggleFolder,
  onMoveNode,
  renderRootActions,
  renderFolderActions,
  renderNodeActions,
}: ExplorerTreeProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [scrollable, setScrollable] = useState(false);
  const [draggedNode, setDraggedNode] = useState<DataNode | null>(null);
  const [dropTarget, setDropTarget] = useState<TreeDropTarget>(null);
  const rootDisplayNameCounts = useMemo(() => buildDisplayNameCounts(nodes), [nodes]);
  const moveEnabled = Boolean(canMoveNodes && onMoveNode);
  const resolvedLoadingPaths = useMemo<ReadonlySet<string>>(
    () => loadingPaths ?? (loadingPath ? new Set([loadingPath]) : EMPTY_PATH_SET),
    [loadingPath, loadingPaths],
  );

  const clearDragState = useCallback(() => {
    setDraggedNode(null);
    setDropTarget(null);
  }, []);

  const setNextDropTarget = useCallback((
    rowPath: string | null,
    targetFolderPath: string | null,
    mode: "folder" | "parent",
    valid: boolean,
  ) => {
    setDropTarget((current) => {
      if (
        current?.rowPath === rowPath
        && current.targetFolderPath === targetFolderPath
        && current.mode === mode
        && current.valid === valid
      ) {
        return current;
      }
      return { rowPath, targetFolderPath, mode, valid };
    });
  }, []);

  const beginNodeDrag = useCallback((event: ReactDragEvent<HTMLButtonElement>, node: DataNode) => {
    if (!moveEnabled) {
      event.preventDefault();
      return;
    }

    event.stopPropagation();
    event.dataTransfer.effectAllowed = "copyMove";
    event.dataTransfer.setData(EXPLORER_TREE_NODE_DRAG_TYPE, node.path);
    event.dataTransfer.setData("text/plain", node.path);
    setDraggedNode(node);
    setDropTarget(null);
  }, [moveEnabled]);

  const dragOverRow = useCallback((
    event: ReactDragEvent<HTMLElement>,
    rowPath: string | null,
    targetFolderPath: string | null,
    mode: "folder" | "parent",
  ) => {
    if (!moveEnabled || !draggedNode) return false;

    const valid = isValidMoveTarget(draggedNode, targetFolderPath);
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = valid ? "move" : "none";
    setNextDropTarget(rowPath, targetFolderPath, mode, valid);
    return valid;
  }, [draggedNode, moveEnabled, setNextDropTarget]);

  const dragLeaveRow = useCallback((event: ReactDragEvent<HTMLElement>, rowPath: string | null) => {
    event.stopPropagation();
  }, []);

  const dropOnRow = useCallback((event: ReactDragEvent<HTMLElement>, targetFolderPath: string | null) => {
    if (!moveEnabled || !draggedNode) return;

    event.preventDefault();
    event.stopPropagation();
    const movingNode = draggedNode;
    const valid = isValidMoveTarget(movingNode, targetFolderPath);
    clearDragState();
    if (!valid || !onMoveNode) return;

    void Promise.resolve(onMoveNode(movingNode, targetFolderPath)).catch((error) => {
      console.error("Unable to move explorer item:", error);
    });
  }, [clearDragState, draggedNode, moveEnabled, onMoveNode]);

  const leaveTree = useCallback((event: ReactDragEvent<HTMLDivElement>) => {
    if (!draggedNode || !didLeaveElementBounds(event)) return;
    setDropTarget(null);
  }, [draggedNode]);

  const dragController = useMemo<TreeDragController>(() => ({
    enabled: moveEnabled,
    draggedNode,
    dropTarget,
    onNodeDragStart: beginNodeDrag,
    onNodeDragEnd: clearDragState,
    onRowDragOver: dragOverRow,
    onRowDragLeave: dragLeaveRow,
    onRowDrop: dropOnRow,
  }), [
    beginNodeDrag,
    clearDragState,
    dragLeaveRow,
    dragOverRow,
    draggedNode,
    dropOnRow,
    dropTarget,
    moveEnabled,
  ]);

  useLayoutEffect(() => {
    if (typeof window === "undefined") return;
    const element = scrollRef.current;
    if (!element) return;

    let frame = 0;
    const updateScrollableState = () => {
      frame = 0;
      const nextScrollable = element.scrollHeight - element.clientHeight > 1;
      setScrollable((current) => (current === nextScrollable ? current : nextScrollable));
    };
    const scheduleUpdate = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(updateScrollableState);
    };

    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(scheduleUpdate);
    resizeObserver?.observe(element);
    if (element.firstElementChild) {
      resizeObserver?.observe(element.firstElementChild);
    }

    scheduleUpdate();
    window.addEventListener("resize", scheduleUpdate);

    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      resizeObserver?.disconnect();
      window.removeEventListener("resize", scheduleUpdate);
    };
  }, [nodes, rootError, rootLoading, resolvedLoadingPaths, expandedPaths]);

  return (
    <div
      className={`explorer-tree-shell ${draggedNode ? "is-dragging-node" : ""}`}
      onDragLeave={moveEnabled ? leaveTree : undefined}
    >
      {showRoot && (
        <div className="explorer-tree-root-scope">
          <div
            className={`tree-row root ${dropTarget?.rowPath === null && dropTarget.valid ? "drop-target" : ""} ${dropTarget?.rowPath === null && !dropTarget.valid ? "drop-invalid" : ""}`}
            style={{ "--depth": 0 } as CSSProperties}
            onDragEnter={moveEnabled ? (event) => dragController.onRowDragOver(event, null, null, "folder") : undefined}
            onDragOver={moveEnabled ? (event) => dragController.onRowDragOver(event, null, null, "folder") : undefined}
            onDragLeave={moveEnabled ? (event) => dragController.onRowDragLeave(event, null) : undefined}
            onDrop={moveEnabled ? (event) => dragController.onRowDrop(event, null) : undefined}
          >
            <span className="tree-row-content">
              <span className="tree-label">{rootLabel}</span>
              {renderRootActions && (
                <span className="tree-row-actions root-actions" onClick={(event) => event.stopPropagation()}>
                  {renderRootActions()}
                </span>
              )}
            </span>
          </div>
        </div>
      )}

      <div ref={scrollRef} className={`explorer-tree-scroll ${scrollable ? "is-scrollable" : ""}`}>
        <div className="explorer-tree-list">
          {rootError && nodes.length === 0 ? (
            <ExplorerTreeMetaRow depth={0}>{rootError}</ExplorerTreeMetaRow>
          ) : rootLoading && nodes.length === 0 ? (
            <ExplorerTreeMetaRow depth={0}>{loadingLabel}</ExplorerTreeMetaRow>
          ) : nodes.length === 0 ? (
            <ExplorerTreeMetaRow depth={0}>{emptyLabel}</ExplorerTreeMetaRow>
          ) : (
            nodes.map((node) => (
              <TreeNodeRow
                key={node.path}
                node={node}
                depth={0}
                siblingDisplayNameCounts={rootDisplayNameCounts}
                expandedPaths={expandedPaths}
                activePath={activePath}
                loadingPaths={resolvedLoadingPaths}
                emptyLabel={emptyLabel}
                loadingLabel={loadingLabel}
                fileIconTheme={fileIconTheme}
                onToggleFolder={onToggleFolder}
                onSelectNode={onSelectNode}
                dragController={dragController}
                renderFolderActions={renderFolderActions}
                renderNodeActions={renderNodeActions}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function TreeNodeRow({
  node,
  depth,
  siblingDisplayNameCounts,
  expandedPaths,
  activePath,
  loadingPaths,
  emptyLabel,
  loadingLabel,
  fileIconTheme,
  onToggleFolder,
  onSelectNode,
  dragController,
  renderFolderActions,
  renderNodeActions,
}: {
  node: DataNode;
  depth: number;
  siblingDisplayNameCounts: Map<string, number>;
  expandedPaths: ReadonlySet<string>;
  activePath: string | null;
  loadingPaths: ReadonlySet<string>;
  emptyLabel: string;
  loadingLabel: string;
  fileIconTheme: FileIconThemeId;
  onToggleFolder?: (node: DataNode, expanded: boolean) => void;
  onSelectNode: (node: DataNode) => void;
  dragController: TreeDragController;
  renderFolderActions?: (node: DataNode) => ReactNode;
  renderNodeActions?: (node: DataNode) => ReactNode;
}) {
  const isFolder = node.type === "folder";
  const isExpanded = isFolder && expandedPaths.has(node.path);
  const hoverExpandTimer = useRef<number | null>(null);
  const active = activePath === node.path;
  const loading = loadingPaths.has(node.path);
  const dragging = dragController.draggedNode?.path === node.path;
  const dropMatchesRow = dragController.dropTarget?.rowPath === node.path;
  const dropOver = dropMatchesRow && dragController.dropTarget?.mode === "folder" && dragController.dropTarget.valid;
  const dropParentOver = dropMatchesRow && dragController.dropTarget?.mode === "parent" && dragController.dropTarget.valid;
  const dropInvalid = dropMatchesRow && !dragController.dropTarget?.valid;
  const children = useMemo(() => node.children ?? [], [node.children]);
  const childDisplayNameCounts = useMemo(() => buildDisplayNameCounts(children), [children]);
  const rowActions = renderNodeActions?.(node) ?? (isFolder ? renderFolderActions?.(node) : null);
  const displayName = useMemo(() => getExplorerDisplayName(node), [node.name, node.type]);
  const showExtensionDisambiguator = Boolean(
    displayName.extension
      && (siblingDisplayNameCounts.get(getDisplayNameKey(displayName.primary)) ?? 0) > 1,
  );

  const toggleCurrentFolder = useCallback(() => {
    if (!isFolder) return;
    onToggleFolder?.(node, !isExpanded);
  }, [isExpanded, isFolder, node, onToggleFolder]);

  const clearHoverExpandTimer = useCallback(() => {
    if (hoverExpandTimer.current === null) return;
    window.clearTimeout(hoverExpandTimer.current);
    hoverExpandTimer.current = null;
  }, []);

  useEffect(() => clearHoverExpandTimer, [clearHoverExpandTimer]);

  const scheduleHoverExpand = useCallback(() => {
    if (!isFolder || isExpanded || hoverExpandTimer.current !== null) return;
    hoverExpandTimer.current = window.setTimeout(() => {
      hoverExpandTimer.current = null;
      onToggleFolder?.(node, true);
    }, FOLDER_HOVER_EXPAND_MS);
  }, [isExpanded, isFolder, node, onToggleFolder]);

  const getDropIntent = useCallback((event: ReactDragEvent<HTMLElement>): {
    targetFolderPath: string | null;
    mode: "folder" | "parent";
  } => {
    if (!isFolder) {
      return { targetFolderPath: getParentPath(node.path), mode: "parent" };
    }

    if (isPointerInFolderPeerDropZone(event)) {
      return { targetFolderPath: getParentPath(node.path), mode: "parent" };
    }

    return { targetFolderPath: node.path, mode: "folder" };
  }, [isFolder, node.path]);

  return (
    <>
      <button
        className={`tree-row ${isFolder ? "folder" : "file"} ${active ? "active" : ""} ${loading ? "loading" : ""} ${dragging ? "dragging" : ""} ${dropOver ? "drop-target" : ""} ${dropParentOver ? "drop-parent-target" : ""} ${dropInvalid ? "drop-invalid" : ""} ${node.status ? `status-${node.status}` : ""}`}
        type="button"
        draggable={dragController.enabled}
        aria-current={active ? "true" : undefined}
        aria-expanded={isFolder ? isExpanded : undefined}
        aria-busy={loading || undefined}
        aria-grabbed={dragging ? "true" : undefined}
        aria-label={node.name}
        title={displayName.hidden || showExtensionDisambiguator ? node.name : undefined}
        onDragStart={(event) => dragController.onNodeDragStart(event, node)}
        onDragEnd={dragController.onNodeDragEnd}
        onDragEnter={(event) => {
          const dropIntent = getDropIntent(event);
          const validTarget = dragController.onRowDragOver(event, node.path, dropIntent.targetFolderPath, dropIntent.mode);
          if (isFolder && dropIntent.mode === "folder" && validTarget) scheduleHoverExpand();
        }}
        onDragOver={(event) => {
          const dropIntent = getDropIntent(event);
          const validTarget = dragController.onRowDragOver(event, node.path, dropIntent.targetFolderPath, dropIntent.mode);
          if (isFolder && dropIntent.mode === "folder" && validTarget) scheduleHoverExpand();
        }}
        onDragLeave={(event) => {
          clearHoverExpandTimer();
          dragController.onRowDragLeave(event, node.path);
        }}
        onDrop={(event) => {
          clearHoverExpandTimer();
          const dropIntent = getDropIntent(event);
          dragController.onRowDrop(event, dropIntent.targetFolderPath);
        }}
        onClick={(event) => {
          event.stopPropagation();
          if (isFolder) {
            toggleCurrentFolder();
            return;
          }
          onSelectNode(node);
        }}
        style={{ "--depth": depth } as CSSProperties}
      >
        <span className="tree-row-content">
          <span
            className="tree-icon-slot"
            onClick={(event) => {
              if (!isFolder) return;
              event.stopPropagation();
              toggleCurrentFolder();
            }}
          >
            {isFolder ? (
              <TreeDisclosureMarker expanded={isExpanded} />
            ) : (
              <FileGlyphIcon name={node.name} type={node.type} size={18} theme={fileIconTheme} />
            )}
          </span>
          <span className="tree-label">
            <span className="tree-label-primary">{displayName.primary}</span>
            {showExtensionDisambiguator && (
              <span className="tree-label-extension" aria-hidden="true">
                {displayName.extension}
              </span>
            )}
          </span>
          {node.status && node.status !== "clean" && (
            <span className={`tree-status ${node.status}`}>{shortStatus(node.status)}</span>
          )}
          {loading && <span className="tree-loading-dot" aria-hidden />}
          {rowActions && (
            <span className="tree-row-actions" onClick={(event) => event.stopPropagation()}>
              {rowActions}
            </span>
          )}
        </span>
      </button>

      {isFolder && (
        <ExplorerSubtreePresence expanded={isExpanded} guideDepth={depth + 1}>
          {loading && children.length === 0 && (
            <ExplorerTreeMetaRow depth={depth + 1}>{loadingLabel}</ExplorerTreeMetaRow>
          )}
          {!loading && children.length === 0 && node.children && (
            <ExplorerTreeMetaRow depth={depth + 1}>{emptyLabel}</ExplorerTreeMetaRow>
          )}
          {children.map((child) => (
            <TreeNodeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              siblingDisplayNameCounts={childDisplayNameCounts}
              expandedPaths={expandedPaths}
              activePath={activePath}
              loadingPaths={loadingPaths}
              emptyLabel={emptyLabel}
              loadingLabel={loadingLabel}
              fileIconTheme={fileIconTheme}
              onToggleFolder={onToggleFolder}
              onSelectNode={onSelectNode}
              dragController={dragController}
              renderFolderActions={renderFolderActions}
              renderNodeActions={renderNodeActions}
            />
          ))}
        </ExplorerSubtreePresence>
      )}
    </>
  );
}

function TreeDisclosureMarker({
  expanded = false,
  size = 12,
}: {
  expanded?: boolean;
  size?: number;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="tree-disclosure-marker"
      style={{
        transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
      }}
    >
      <path d="M4 2.5 7.5 6 4 9.5" />
    </svg>
  );
}

function ExplorerTreeMetaRow({
  depth,
  children,
}: {
  depth: number;
  children: ReactNode;
}) {
  return (
    <div className="tree-meta-row" style={{ "--depth": depth } as CSSProperties}>
      <span>{children}</span>
    </div>
  );
}

function TreeSubtreeGuide({ depth }: { depth: number }) {
  if (depth <= 0) return null;
  return <span className="tree-subtree-guide" style={{ "--depth": depth } as CSSProperties} aria-hidden />;
}

function ExplorerSubtreePresence({
  expanded,
  guideDepth,
  children,
}: {
  expanded: boolean;
  guideDepth: number;
  children: ReactNode;
}) {
  const mountedOnceRef = useRef(false);
  const [renderSubtree, setRenderSubtree] = useState(expanded);

  useLayoutEffect(() => {
    if (expanded) setRenderSubtree(true);
  }, [expanded]);

  useEffect(() => {
    mountedOnceRef.current = true;
  }, []);

  if (!expanded && !renderSubtree) return null;

  return (
    <ExplorerSubtreeMotion
      visible={expanded}
      guideDepth={guideDepth}
      animateInitialEnter={mountedOnceRef.current}
      onExited={() => setRenderSubtree(false)}
    >
      {children}
    </ExplorerSubtreeMotion>
  );
}

function getSubtreeMotionDurationMs(fromHeight: number, toHeight: number): number {
  const distance = Math.abs(toHeight - fromHeight);
  return Math.round(
    Math.min(
      SUBTREE_MOTION_MAX_MS,
      Math.max(
        SUBTREE_MOTION_MIN_MS,
        SUBTREE_MOTION_MIN_MS + distance * SUBTREE_MOTION_PX_FACTOR,
      ),
    ),
  );
}

function ExplorerSubtreeMotion({
  visible,
  guideDepth,
  animateInitialEnter,
  onExited,
  children,
}: {
  visible: boolean;
  guideDepth: number;
  animateInitialEnter: boolean;
  onExited: () => void;
  children: ReactNode;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number | null>(null);
  const onExitedRef = useRef(onExited);
  const mountedRef = useRef(false);
  const [height, setHeight] = useState<number | "auto">(() => (
    visible && !animateInitialEnter ? "auto" : 0
  ));
  const [durationMs, setDurationMs] = useState(SUBTREE_MOTION_MIN_MS);

  const cancelFrame = useCallback(() => {
    if (rafRef.current === null) return;
    window.cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
  }, []);

  useEffect(() => cancelFrame, [cancelFrame]);

  useEffect(() => {
    onExitedRef.current = onExited;
  }, [onExited]);

  useLayoutEffect(() => {
    const wrapper = wrapperRef.current;
    const content = contentRef.current;
    if (!wrapper || !content) return;

    cancelFrame();

    if (!mountedRef.current) {
      mountedRef.current = true;
      if (!visible) {
        setHeight(0);
        return;
      }
      if (!animateInitialEnter) {
        setHeight("auto");
        return;
      }
      const nextHeight = content.scrollHeight;
      setDurationMs(getSubtreeMotionDurationMs(0, nextHeight));
      setHeight(0);
      rafRef.current = window.requestAnimationFrame(() => {
        setHeight(nextHeight);
        rafRef.current = null;
      });
      return;
    }

    const currentHeight = wrapper.getBoundingClientRect().height;
    const nextHeight = visible ? content.scrollHeight : 0;

    if (!visible && Math.abs(currentHeight - nextHeight) < 1) {
      setHeight(0);
      onExitedRef.current();
      return;
    }

    setDurationMs(getSubtreeMotionDurationMs(currentHeight, nextHeight));
    setHeight(currentHeight);
    rafRef.current = window.requestAnimationFrame(() => {
      setHeight(nextHeight);
      rafRef.current = null;
    });
  }, [animateInitialEnter, cancelFrame, visible]);

  useEffect(() => {
    if (!visible || height === "auto" || typeof ResizeObserver === "undefined") {
      return undefined;
    }

    const wrapper = wrapperRef.current;
    const content = contentRef.current;
    if (!wrapper || !content) return undefined;

    let previousHeight = content.scrollHeight;
    const observer = new ResizeObserver(() => {
      const nextHeight = content.scrollHeight;
      if (Math.abs(nextHeight - previousHeight) < 1) return;
      previousHeight = nextHeight;

      cancelFrame();
      const currentHeight = wrapper.getBoundingClientRect().height;
      setDurationMs(getSubtreeMotionDurationMs(currentHeight, nextHeight));
      setHeight(currentHeight);
      rafRef.current = window.requestAnimationFrame(() => {
        setHeight(nextHeight);
        rafRef.current = null;
      });
    });

    observer.observe(content);
    return () => observer.disconnect();
  }, [cancelFrame, height, visible]);

  return (
    <div
      ref={wrapperRef}
      className="tree-subtree-motion"
      onTransitionEnd={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.propertyName !== "height") return;
        if (!visible) {
          onExitedRef.current();
          return;
        }
        setHeight("auto");
      }}
      style={{
        "--tree-motion-duration": `${durationMs}ms`,
        "--tree-motion-ease": SUBTREE_MOTION_EASE,
        height: height === "auto" ? "auto" : `${Math.max(0, height)}px`,
      } as CSSProperties}
    >
      <div ref={contentRef} className="tree-subtree-content">
        <TreeSubtreeGuide depth={guideDepth} />
        {children}
      </div>
    </div>
  );
}

function shortStatus(status: NonNullable<DataNode["status"]>) {
  if (status === "modified") return "M";
  if (status === "created") return "A";
  if (status === "deleted") return "D";
  if (status === "moved") return "R";
  return "";
}

function isValidMoveTarget(node: DataNode, targetFolderPath: string | null): boolean {
  if (getParentPath(node.path) === targetFolderPath) return false;
  if (targetFolderPath === node.path) return false;
  if (targetFolderPath?.startsWith(`${node.path}/`)) return false;
  return true;
}

function getParentPath(path: string): string | null {
  if (!path.includes("/")) return null;
  return path.slice(0, path.lastIndexOf("/"));
}

function didLeaveElementBounds(event: ReactDragEvent<HTMLElement>): boolean {
  const rect = event.currentTarget.getBoundingClientRect();
  return (
    event.clientX < rect.left
    || event.clientX > rect.right
    || event.clientY < rect.top
    || event.clientY > rect.bottom
  );
}

function isPointerInFolderPeerDropZone(event: ReactDragEvent<HTMLElement>): boolean {
  const rect = event.currentTarget.getBoundingClientRect();
  const peerZoneTop = rect.bottom - rect.height * FOLDER_PEER_DROP_ZONE_RATIO;
  return event.clientY >= peerZoneTop;
}

function getExplorerDisplayName(node: DataNode): {
  primary: string;
  extension: string | null;
  hidden: boolean;
} {
  if (node.type === "folder") {
    return { primary: node.name, extension: null, hidden: false };
  }

  const extension = getDisplayExtension(node.name);
  if (!extension) {
    return { primary: node.name, extension: null, hidden: false };
  }

  const suffix = `.${extension}`;
  const primary = node.name.slice(0, -suffix.length);
  if (!primary) {
    return { primary: node.name, extension: null, hidden: false };
  }

  return { primary, extension, hidden: true };
}

function buildDisplayNameCounts(nodes: readonly DataNode[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const node of nodes) {
    const displayName = getExplorerDisplayName(node);
    const key = getDisplayNameKey(displayName.primary);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}

function getDisplayNameKey(value: string): string {
  return value.toLocaleLowerCase();
}

function getDisplayExtension(name: string): string | null {
  if (!name) return null;
  if (name.startsWith(".") && name.indexOf(".", 1) === -1) return null;

  const extension = getMatchedExtension(name);
  if (!extension) return null;

  const suffix = `.${extension}`;
  if (!name.toLocaleLowerCase().endsWith(suffix.toLocaleLowerCase())) return null;
  if (name.length <= suffix.length) return null;
  return extension;
}

const EMPTY_PATH_SET: ReadonlySet<string> = new Set();
