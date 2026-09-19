'use client';

import { ItemActionMenu } from '@/components/ItemActionMenu';
import { PageLoading } from '@/components/loading';
import { CountBadge } from '@/components/ui/CountBadge';
import { FilePreviewIcon } from '@/lib/fileIcons';
import { useNodeDrop } from '@/lib/hooks/useNodeDrop';
import { getNodeTypeConfig, getSyncSource, getSyncSourceIcon, isSyncedType, LockIcon } from '@/lib/nodeTypeConfig';
import { useEffect, useRef, useState } from 'react';

// Content type definition
export type ContentType = 'folder' | 'json' | 'markdown' | 'image' | 'pdf' | 'video' | 'file' | 'sync' | 'github_repo' | 'notion_page' | 'notion_database' | 'airtable_base' | 'linear_project' | 'google_sheets';

const GRID_ITEM_LABEL_WIDTH = 108;

function GridItemName({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <span
      title={title}
      className="block text-center text-[13px] font-medium text-[var(--po-text-subtle)] transition-colors group-hover:text-[var(--po-text)]"
      style={{
        width: GRID_ITEM_LABEL_WIDTH,
        maxWidth: '100%',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  );
}

// --- Finder-style Preview Icons ---

// Document shell: paper shape with fold corner, content area for preview
const DocShell = ({ children }: { children?: React.ReactNode }) => (
  <div style={{ position: 'relative', width: 52, height: 58 }}>
    <svg width="52" height="58" viewBox="0 0 64 72" fill="none" style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%' }}>
      {/* Shadow */}
      <path
        d="M10 6C10 4.89543 10.8954 4 12 4H43L57 18V69C57 70.1046 56.1046 71 55 71H12C10.8954 71 10 70.1046 10 69V6Z"
        fill="var(--po-file-icon-shadow)"
      />
      {/* Paper body */}
      <path
        d="M8 4C8 2.89543 8.89543 2 10 2H42L56 16V68C56 69.1046 55.1046 70 54 70H10C8.89543 70 8 69.1046 8 68V4Z"
        fill="var(--po-file-icon-body)"
        stroke="var(--po-file-icon-stroke)"
        strokeWidth="1"
      />
      {/* Fold corner */}
      <path d="M42 2V16H56" stroke="var(--po-file-icon-stroke)" strokeWidth="1" strokeLinejoin="round" />
      <path d="M42 2V16H56L42 2Z" fill="var(--po-file-icon-fold)" />
    </svg>
    <div style={{
      position: 'absolute',
      top: 13,
      left: 9,
      right: 7,
      bottom: 4,
      overflow: 'hidden',
    }}>
      {children}
    </div>
  </div>
);

// Folder icon with children count badge
const FolderIconLarge = ({ childrenCount }: { childrenCount?: number | null }) => (
  <div style={{ position: 'relative', width: 56, height: 56, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
    <img src="/icons/folder.svg" alt="Folder" width={56} height={56} style={{ display: 'block' }} />
    {childrenCount != null && childrenCount > 0 && (
      <span style={{ position: 'absolute', bottom: 0, right: -4 }}>
        <CountBadge value={childrenCount} size="md" tone="surface" />
      </span>
    )}
  </div>
);

// Markdown preview: actual text content at tiny size
const MarkdownPreviewIcon = ({ snippet }: { snippet?: string | null }) => (
  <DocShell>
    {snippet ? (
      <div style={{
        fontSize: 5,
        lineHeight: 1.45,
        color: 'var(--po-text-muted)',
        fontFamily: 'var(--po-font-sans)',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        overflow: 'hidden',
        height: '100%',
      }}>
        {snippet}
      </div>
    ) : (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3.5, paddingTop: 1 }}>
        {[92, 58, 78, 48, 85, 62, 72, 52].map((w, i) => (
          <div key={i} style={{ height: 2, background: 'var(--po-text-disabled)', borderRadius: 1, width: `${w}%` }} />
        ))}
      </div>
    )}
  </DocShell>
);

const JsonPreviewIcon = ({ snippet }: { snippet?: string | null }) => (
  <DocShell>
    {snippet ? (
      <div style={{
        fontSize: 5,
        lineHeight: 1.5,
        color: 'var(--po-success)',
        fontFamily: 'var(--po-font-sans)',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        overflow: 'hidden',
        height: '100%',
      }}>
        {snippet}
      </div>
    ) : (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100%',
        fontSize: 8, fontWeight: 600,
        color: 'var(--po-success)',
        fontFamily: 'var(--po-font-sans)',
        letterSpacing: '0.5px',
      }}>
        {'{ }'}
      </div>
    )}
  </DocShell>
);

// File icon: document shell with extension in center
const FileIconLarge = ({ ext }: { ext: string }) => (
  <DocShell>
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      height: '100%',
    }}>
      <span style={{
        fontSize: 12,
        fontWeight: 800,
        color: 'var(--po-text-subtle)',
        fontFamily: 'var(--po-font-sans)',
        letterSpacing: 0.5,
        textTransform: 'uppercase',
      }}>
        {ext}
      </span>
    </div>
  </DocShell>
);

// Branded icon for synced sources (Notion, GitHub, etc.)
// Design principle: App logo centered + type badge (JSON/MD/folder) bottom-right
const UnifiedBrandedIcon = ({
  BadgeIcon,
  type,
  badgeSize = 32,
  showWarning = false,
  snippet,
  direction = 'inbound' as 'inbound' | 'outbound' | 'bidirectional',
}: {
  BadgeIcon?: React.ElementType;
  type: string;
  badgeSize?: number;
  showWarning?: boolean;
  snippet?: string | null;
  direction?: 'inbound' | 'outbound' | 'bidirectional';
}) => {
  const typeConfig = getNodeTypeConfig(type);

  const badgeLines = (snippet || '').split('\n').slice(0, 5);
  const truncated = (s: string, max: number) => s.length > max ? s.slice(0, max) : s;

  const isJson = typeConfig.iconCategory !== 'markdown' && typeConfig.iconCategory !== 'folder';
  const badgeColor = isJson ? 'var(--po-success)' : 'var(--po-text-muted)';

  const DataBadge = () => (
    <svg width="30" height="36" viewBox="0 0 30 36" fill="none">
      <path d="M2 3C2 1.895 2.895 1 4 1H19L28 10V33C28 34.105 27.105 35 26 35H4C2.895 35 2 34.105 2 33V3Z" fill="var(--po-file-icon-body)" stroke="var(--po-file-icon-stroke)" strokeWidth="1" />
      <path d="M19 1V10H28" stroke="var(--po-file-icon-stroke)" strokeWidth="1" strokeLinejoin="round" />
      <path d="M19 1V10H28L19 1Z" fill="var(--po-file-icon-fold)" />
      {badgeLines.length > 0 ? (
        <text x="4" y="14" fontSize="3.4" fill={badgeColor} fontFamily={isJson ? 'var(--po-font-sans)' : 'var(--po-font-sans)'}>
          {(isJson ? badgeLines : (snippet || '').split(/\s+/).reduce<string[]>((lines, word) => {
            const last = lines[lines.length - 1] || '';
            if (lines.length === 0 || last.length + word.length > 9) lines.push(word);
            else lines[lines.length - 1] = last + ' ' + word;
            return lines;
          }, [])).slice(0, 5).map((line, i) => (
            <tspan key={i} x="4" dy={i === 0 ? 0 : 4}>{truncated(line, 10)}</tspan>
          ))}
        </text>
      ) : (
        <g transform="translate(4, 13)">
          {[10, 17, 12, 15, 8].map((w, i) => (
            <rect key={i} y={i * 3.5} width={w} height="1.2" rx="0.4" fill={badgeColor} opacity={0.9 - i * 0.1} />
          ))}
        </g>
      )}
    </svg>
  );

  const FolderBadge = () => (
    <img src="/icons/folder.svg" alt="Folder" width={24} height={24} style={{ display: 'block', filter: 'drop-shadow(0 1px 3px var(--po-file-icon-shadow))' }} />
  );

  const ConnectorArrow = () => {
    const color = 'var(--po-text-subtle)';
    const elbowPath = 'M 16 30 L 16 42 Q 16 46, 20 46 L 32 46';
    const elbowPathReversed = 'M 32 46 L 20 46 Q 16 46, 16 42 L 16 30';
    return (
      <svg
        width="64" height="64" viewBox="0 0 64 64" fill="none"
        style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 5 }}
      >
        <defs>
          <marker id="arr-r" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
            <path d="M0,0 L0,5 L5,2.5 z" fill={color} />
          </marker>
          <marker id="arr-u" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
            <path d="M0,0 L0,5 L5,2.5 z" fill={color} />
          </marker>
        </defs>

        {direction === 'inbound' && (
          <path d={elbowPath} stroke={color} strokeWidth="1.3" fill="none" markerEnd="url(#arr-r)" />
        )}

        {direction === 'outbound' && (
          <path d={elbowPathReversed} stroke={color} strokeWidth="1.3" fill="none" markerEnd="url(#arr-u)" />
        )}

        {direction === 'bidirectional' && (
          <>
            <path d={elbowPath} stroke={color} strokeWidth="1.3" fill="none" markerEnd="url(#arr-r)" />
            <path d={elbowPathReversed} stroke={color} strokeWidth="1.3" fill="none" markerEnd="url(#arr-u)" />
          </>
        )}
      </svg>
    );
  };

  return (
    <div style={{ position: 'relative', width: 64, height: 72, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      {/* 64x64 square content, vertically centered in 64x72 wrapper */}
      <div style={{ position: 'relative', width: 64, height: 64 }}>
        {/* App Logo - top-left, max 28x28 */}
        <div style={{
          position: 'absolute',
          top: 2,
          left: 2,
          zIndex: 10,
          filter: 'drop-shadow(0 2px 4px var(--po-file-icon-shadow))',
        }}>
          {BadgeIcon && (
            <div style={{ maxWidth: 28, maxHeight: 28 }}>
              <BadgeIcon size={28} />
            </div>
          )}
        </div>

        {/* Directional connector */}
        <ConnectorArrow />

        {/* Data badge - bottom-right, full size */}
        <div style={{
          position: 'absolute',
          bottom: 0,
          right: 0,
          zIndex: 10,
          filter: 'drop-shadow(0 2px 4px var(--po-file-icon-shadow))',
        }}>
          {typeConfig.iconCategory === 'folder' ? <FolderBadge /> : <DataBadge />}
        </div>

        {/* Warning indicator */}
        {showWarning && (
          <div style={{
            position: 'absolute',
            top: 0,
            right: 0,
            width: 16,
          height: 16,
          borderRadius: 4,
          background: 'var(--po-warning)',
          border: '2px solid var(--po-panel)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 20,
        }}>
          <span style={{ color: 'var(--po-text-inverse)', fontSize: 10, fontWeight: 800 }}>!</span>
        </div>
      )}
      </div>
    </div>
  );
};

const CreateIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--po-text-subtle)" strokeWidth="2">
    <path d="M12 5V19M5 12H19" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

// Agent resource type for props
export interface AgentResource {
  path: string;
  readonly: boolean;
}

export interface GridViewItem {
  id: string;
  name: string;
  type: ContentType;  // type 直接决定渲染方式
  description?: string;
  rowCount?: number;
  version_path?: string;
  sync_url?: string | null;
  thumbnailUrl?: string;
  onClick: (e: React.MouseEvent) => void;
  // 同步相关字段
  is_synced?: boolean;
  sync_source?: string | null;  // 从 type 提取，如 github_repo → github
  sync_status?: 'not_connected' | 'idle' | 'syncing' | 'error';
  last_synced_at?: string | null;
  // Finder-style preview data
  preview_snippet?: string | null;
  children_count?: number | null;
}

export interface GridViewProps {
  items: GridViewItem[];
  parentFolderId?: string | null;
  onCreateClick?: (e: React.MouseEvent) => void;
  onRename?: (id: string, currentName: string) => void;
  onDelete?: (id: string, name: string) => void;
  onDuplicate?: (id: string) => void;
  onRefresh?: (id: string) => void;
  onMove?: (id: string, name: string, version_path?: string) => void;
  onMoveNode?: (nodeId: string, targetFolderId: string | null, sourceParentId?: string | null) => Promise<void>;
  onCreateTool?: (id: string, name: string, type: string) => void;
  onShareWithAI?: (path: string) => void;
  loading?: boolean;
  agentResources?: AgentResource[];
  highlightNodeId?: string | null;
  // ─── Multi-select ───
  selectedIds?: Set<string>;
  onToggleSelected?: (id: string) => void;
  onRangeSelectTo?: (id: string) => void;
  onSelectOnly?: (id: string) => void;
  onClearSelection?: () => void;
}

function GridItem({
  item,
  parentFolderId,
  agentResource,
  onRename,
  onDelete,
  onDuplicate,
  onRefresh,
  onMove,
  onMoveNode,
  onCreateTool,
  onShareWithAI,
  isHighlighted,
  isSelected,
  selectionActive,
  onToggleSelected,
  onRangeSelectTo,
  onSelectOnly,
}: {
  item: GridViewItem;
  parentFolderId?: string | null;
  agentResource?: AgentResource;
  onRename?: (id: string, currentName: string) => void;
  onDelete?: (id: string, name: string) => void;
  onDuplicate?: (id: string) => void;
  onRefresh?: (id: string) => void;
  onMove?: (id: string, name: string, version_path?: string) => void;
  onMoveNode?: (nodeId: string, targetFolderId: string | null, sourceParentId?: string | null) => Promise<void>;
  onCreateTool?: (id: string, name: string, type: string) => void;
  onShareWithAI?: (path: string) => void;
  isHighlighted?: boolean;
  isSelected?: boolean;
  selectionActive?: boolean;
  onToggleSelected?: (id: string) => void;
  onRangeSelectTo?: (id: string) => void;
  onSelectOnly?: (id: string) => void;
}) {
  const [hovered, setHovered] = useState(false);
  const itemRef = useRef<HTMLDivElement>(null);

  const typeConfig = getNodeTypeConfig(item.type);
  const isFolder = typeConfig.iconCategory === 'folder';
  const { isDropTarget, dropHandlers } = useNodeDrop({
    targetFolderId: item.id,
    onMoveNode,
    disabled: !isFolder,
  });

  useEffect(() => {
    if (isHighlighted && itemRef.current) {
      itemRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [isHighlighted]);

  // Check if this item has agent access
  const hasAgentAccess = !!agentResource;
  const accessMode = agentResource?.readonly ? 'read' : 'write';

  // 判断是否为同步类型
  const isSynced = item.is_synced || isSyncedType(item.type);
  // 从 sync_source 或 type 获取来源，用于显示 Logo
  const syncSource = item.sync_source || getSyncSource(item.type);
  // 根据 source 获取对应的 Logo 图标
  const BadgeIcon = getSyncSourceIcon(syncSource) || typeConfig.badgeIcon;
  const isPlaceholder = item.sync_status === 'not_connected';

  // 格式化来源名称
  const formatSourceName = (source: string | null) => {
    if (!source) return null;
    const names: Record<string, string> = {
      'github': 'GitHub',
      'notion': 'Notion',
      'airtable': 'Airtable',
      'linear': 'Linear',
      'sheets': 'Sheets',
      'gmail': 'Gmail',
      'drive': 'Drive',
      'calendar': 'Calendar',
      'docs': 'Docs',
    };
    return names[source] || source;
  };

  const getTypeIcon = () => {
    if (isSynced) {
      return (
        <UnifiedBrandedIcon
          BadgeIcon={BadgeIcon}
          type={item.type}
          badgeSize={32}
          showWarning={isPlaceholder}
          snippet={item.preview_snippet}
        />
      );
    }

    switch (typeConfig.iconCategory) {
      case 'folder':
        return <FilePreviewIcon name={item.name} type="folder" size={56} childrenCount={item.children_count} />;
      case 'markdown':
        return <FilePreviewIcon name={item.name} type="markdown" size={52} snippet={item.preview_snippet} />;
      case 'file':
        return <FilePreviewIcon name={item.name} type={item.type} size={52} snippet={item.preview_snippet} />;
      default:
        return <FilePreviewIcon name={item.name} type={item.type} size={52} snippet={item.preview_snippet} />;
    }
  };

  // Modifier-aware click handler. Branches:
  //   - Cmd/Ctrl  → toggle this item in/out of selection
  //   - Shift     → extend selection to this item
  //   - Plain     → if a selection exists, clear it (the click resets
  //                  to "single mode") AND open the item; otherwise
  //                  just open. Matches Finder/Explorer.
  const handleClick = (e: React.MouseEvent) => {
    const isMod = e.metaKey || e.ctrlKey;
    if (isMod && onToggleSelected) {
      e.preventDefault();
      e.stopPropagation();
      onToggleSelected(item.id);
      return;
    }
    if (e.shiftKey && onRangeSelectTo) {
      e.preventDefault();
      e.stopPropagation();
      onRangeSelectTo(item.id);
      return;
    }
    if (selectionActive && onSelectOnly) {
      // Drop multi-select context but keep this item highlighted as
      // the new anchor. The default item.onClick still fires so the
      // user transitions cleanly into single-file mode.
      onSelectOnly(item.id);
    }
    item.onClick(e);
  };

  return (
    <div
      ref={itemRef}
      onClick={handleClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      draggable={!isPlaceholder}
      onDragStart={(e) => {
        if (isPlaceholder) {
          e.preventDefault();
          return;
        }
        e.dataTransfer.setData('application/x-puppyone-node', JSON.stringify({
          id: item.id,
          name: item.name,
          type: item.type,
          parentId: parentFolderId ?? null,
        }));
        e.dataTransfer.effectAllowed = 'copyMove';
      }}
      {...dropHandlers}
      className={`flex flex-col items-center justify-center gap-1.5 cursor-pointer group p-3 rounded-xl transition-colors relative aspect-square ${
        isDropTarget ? 'bg-[var(--po-selected)] ring-2 ring-[var(--po-focus-ring)]' :
        isSelected ? 'bg-[var(--po-selected)] ring-2 ring-[var(--po-focus-ring)]' :
        isHighlighted ? 'bg-[var(--po-selected)] ring-2 ring-[var(--po-focus-ring)]' :
        hasAgentAccess ? 'ring-2 ring-[color-mix(in_srgb,var(--po-warning)_50%,transparent)]' :
        hovered ? 'bg-[var(--po-hover)]' : 'bg-transparent'
      }`}
      style={{
        animation: isHighlighted ? 'gridItemHighlight 2s ease-out' : undefined,
      }}
    >
      {/* 图标区域 */}
      <div className="flex items-center justify-center w-14 h-14 opacity-80 group-hover:opacity-100 transition-opacity drop-shadow-sm relative" title={isPlaceholder ? "Click to connect" : undefined}>
        {getTypeIcon()}
      </div>

      {isFolder && onShareWithAI && !isPlaceholder && !isSelected && (
        <button
          type="button"
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            onShareWithAI(item.id);
          }}
          className="opacity-0 group-hover:opacity-100 transition-opacity"
          style={{
            position: 'absolute',
            top: 6,
            left: '50%',
            transform: 'translateX(-50%)',
            height: 24,
            padding: '0 9px',
            borderRadius: 6,
            border: '1px solid var(--po-border)',
            background: 'var(--po-panel-raised)',
            color: 'var(--po-text-muted)',
            boxShadow: '0 8px 22px var(--po-shadow)',
            fontSize: 11,
            fontWeight: 600,
            fontFamily: 'var(--po-font-sans)',
            whiteSpace: 'nowrap',
            cursor: 'pointer',
            zIndex: 22,
          }}
          title={`Share ${item.name} with AI`}
          aria-label={`Share ${item.name} with AI`}
        >
          Share with AI
        </button>
      )}

      {/* Action Menu - 右上角 (absolute 定位相对于 GridItem).
          Hidden while the item is selected so the checkmark badge
          can occupy the corner without overlapping. */}
      {(onRename || onDelete || onDuplicate || onMove || onCreateTool || (isSynced && onRefresh)) && !isPlaceholder && !isSelected && (
        <div style={{ position: 'absolute', top: 4, right: 4 }}>
          <ItemActionMenu
            itemId={item.id}
            itemName={item.name}
            itemType={item.type}
            onRename={onRename}
            onDelete={onDelete}
            onDuplicate={onDuplicate}
            onMove={onMove ? (id, name) => onMove(id, name, item.version_path) : undefined}
            onRefresh={isSynced ? onRefresh : undefined}
            onCreateTool={onCreateTool}
            syncUrl={item.sync_url}
            visible={hovered}
          />
        </div>
      )}

      {/* Selection checkmark - 右上角 (only when multi-selected) */}
      {isSelected && (
        <div
          style={{
            position: 'absolute',
            top: 4,
            right: 4,
            width: 18,
            height: 18,
            borderRadius: '50%',
            background: 'var(--po-accent)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: '0 0 0 2px var(--po-panel)',
            zIndex: 25,
          }}
          aria-label="Selected"
        >
          <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
            <path
              d="M2.5 6.5L4.75 8.75L9.5 4"
              stroke="var(--po-text-inverse)"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
      )}

      {/* Read-only Lock Icon - 右上角 */}
      {typeConfig.isReadOnly && !isPlaceholder && (
        <div style={{
          position: 'absolute',
          top: 4,
          right: 4,
          color: 'var(--po-text-disabled)',
          display: 'flex',
          alignItems: 'center',
        }}>
          <LockIcon size={12} />
        </div>
      )}

      {/* Agent Access Badge - 左上角 */}
      {hasAgentAccess && (
        <div
          style={{
            position: 'absolute',
            top: 4,
            left: 4,
            padding: '2px 6px',
            borderRadius: 3,
            background: accessMode === 'write' ? 'color-mix(in srgb, var(--po-warning) 18%, transparent)' : 'var(--po-control)',
            fontSize: 10,
            fontWeight: 500,
            color: accessMode === 'write' ? 'var(--po-warning)' : 'var(--po-text-muted)',
          }}
        >
          {accessMode === 'write' ? 'Edit' : 'View'}
        </div>
      )}

      {/* Name */}
      <GridItemName
        title={
          isSynced && syncSource && !isPlaceholder
            ? `${item.name} · ${formatSourceName(syncSource) ?? syncSource}`
            : item.name
        }
      >
        {item.name}
        {isSynced && syncSource && !isPlaceholder && (
          <span style={{ color: 'var(--po-text-disabled)', fontSize: 10 }}> · {formatSourceName(syncSource)}</span>
        )}
      </GridItemName>
    </div>
  );
}

function CreateButton({
  onClick,
}: {
  onClick: (e: React.MouseEvent) => void;
}) {
  const [hovered, setHovered] = useState(false);

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="flex flex-col items-center justify-center gap-1.5 cursor-pointer group p-3 rounded-xl hover:bg-[var(--po-hover)] transition-colors aspect-square"
    >
      <div className="flex items-center justify-center w-14 h-14 opacity-80 group-hover:opacity-100 transition-opacity drop-shadow-sm border-2 border-dashed border-[var(--po-border)] group-hover:border-[var(--po-border-strong)] rounded-xl">
        <CreateIcon />
      </div>
      <GridItemName title="New">New</GridItemName>
    </div>
  );
}

export function GridView({
  items,
  parentFolderId,
  onCreateClick,
  onRename,
  onDelete,
  onDuplicate,
  onRefresh,
  onMove,
  onMoveNode,
  onCreateTool,
  onShareWithAI,
  loading,
  agentResources,
  highlightNodeId,
  selectedIds,
  onToggleSelected,
  onRangeSelectTo,
  onSelectOnly,
  onClearSelection,
}: GridViewProps) {
  if (loading) {
    return <PageLoading variant="fill" />;
  }

  const resourceMap = new Map(agentResources?.map(r => [r.path, r]) ?? []);
  const selectionActive = (selectedIds?.size ?? 0) > 0;

  // Click on the grid container itself (not an item or the create
  // button) clears the multi-selection. Matches Finder's
  // "click background to deselect" affordance.
  const handleBackgroundClick = (e: React.MouseEvent) => {
    if (!selectionActive || !onClearSelection) return;
    if (e.target === e.currentTarget) {
      onClearSelection();
    }
  };

  return (
    <>
      <style>{`
        @keyframes gridItemHighlight {
          0% { background: var(--po-selected); outline-color: var(--po-focus-ring); }
          100% { background: transparent; outline-color: transparent; }
        }
      `}</style>
      <div
        className="grid gap-x-2 gap-y-2 w-full"
        style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))' }}
        onClick={handleBackgroundClick}
      >
        {items.map(item => (
          <GridItem
            key={item.id}
            item={item}
            parentFolderId={parentFolderId}
            agentResource={resourceMap.get(item.id)}
            onRename={onRename}
            onDelete={onDelete}
            onDuplicate={onDuplicate}
            onRefresh={onRefresh}
            onMove={onMove}
            onMoveNode={onMoveNode}
            onCreateTool={onCreateTool}
            onShareWithAI={onShareWithAI}
            isHighlighted={highlightNodeId === item.id}
            isSelected={selectedIds?.has(item.id)}
            selectionActive={selectionActive}
            onToggleSelected={onToggleSelected}
            onRangeSelectTo={onRangeSelectTo}
            onSelectOnly={onSelectOnly}
          />
        ))}

        {onCreateClick && (
          <CreateButton onClick={onCreateClick} />
        )}
      </div>
    </>
  );
}
