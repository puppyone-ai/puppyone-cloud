'use client';

import { ItemActionMenu } from '@/components/ItemActionMenu';
import { PageLoading } from '@/components/loading';
import { StatusDot } from '@/components/ui/StatusDot';
import type { AgentResource, ContentType } from '@/features/files/components/views/GridView';
import { FileGlyphIcon } from '@/lib/fileIcons';
import { useNodeDrop } from '@/lib/hooks/useNodeDrop';
import { getNodeTypeConfig, getSyncSource, getSyncSourceIcon, isSyncedType, LockIcon } from '@/lib/nodeTypeConfig';
import { useState } from 'react';

export interface ListViewItem {
  id: string;
  name: string;
  type: ContentType;  // type 直接决定渲染方式
  description?: string;
  rowCount?: number;
  version_path?: string;
  onClick: (e: React.MouseEvent) => void;
  // 同步相关字段
  is_synced?: boolean;
  sync_source?: string | null;  // 从 type 提取，如 github_repo → github
  sync_url?: string | null;
  sync_status?: 'not_connected' | 'idle' | 'syncing' | 'error';
  last_synced_at?: string | null;
}

export interface ListViewProps {
  items: ListViewItem[];
  parentFolderId?: string | null;
  onCreateClick?: (e: React.MouseEvent) => void;
  onRename?: (id: string, currentName: string) => void;
  onDelete?: (id: string, name: string) => void;
  onDuplicate?: (id: string) => void;
  onRefresh?: (id: string) => void;
  onMove?: (id: string, name: string, version_path?: string) => void;
  onMoveNode?: (nodeId: string, targetFolderId: string | null, sourceParentId?: string | null) => Promise<void>;
  onCreateTool?: (id: string, name: string, type: string) => void;
  createLabel?: string;
  loading?: boolean;
  agentResources?: AgentResource[];
}

const ChevronRightIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
    <path d="M9 6L15 12L9 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

// Agent Access Tag
const AgentAccessTag = ({ mode }: { mode: 'read' | 'write' }) => (
  <div
    style={{
      padding: '2px 6px',
      borderRadius: 4,
      background: mode === 'write' ? 'color-mix(in srgb, var(--po-warning) 15%, transparent)' : 'var(--po-control)',
      fontSize: 11,
      fontWeight: 500,
      color: mode === 'write' ? 'var(--po-warning)' : 'var(--po-text-muted)',
    }}
  >
    {mode === 'write' ? 'Edit' : 'View'}
  </div>
);

function getIcon(name: string, type: string) {
  return <FileGlyphIcon name={name} type={type} size={16} />;
}

// Sync Status indicator (只显示 syncing/error，占位符不显示任何东西)
const SyncStatusIndicator = ({ status }: { status?: string }) => {
  if (!status || status === 'idle' || status === 'not_connected') return null;

  const configs: Record<string, { color: string; label: string }> = {
    'syncing': { color: 'var(--po-accent)', label: 'Syncing...' },
    'error': { color: 'var(--po-danger)', label: 'Error' },
  };

  const config = configs[status];
  if (!config) return null;

  return (
    <span style={{
      marginLeft: 8,
      padding: '1px 6px',
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 500,
      background: `color-mix(in srgb, ${config.color} 12%, transparent)`,
      color: config.color,
    }}>
      {config.label}
    </span>
  );
};

function ListItem({
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
}: {
  item: ListViewItem;
  parentFolderId?: string | null;
  agentResource?: AgentResource;
  onRename?: (id: string, currentName: string) => void;
  onDelete?: (id: string, name: string) => void;
  onDuplicate?: (id: string) => void;
  onRefresh?: (id: string) => void;
  onMove?: (id: string, name: string, version_path?: string) => void;
  onMoveNode?: (nodeId: string, targetFolderId: string | null, sourceParentId?: string | null) => Promise<void>;
  onCreateTool?: (id: string, name: string, type: string) => void;
}) {
  const [hovered, setHovered] = useState(false);

  const typeConfig = getNodeTypeConfig(item.type);
  const isFolder = typeConfig.iconCategory === 'folder';

  const { isDropTarget, dropHandlers } = useNodeDrop({
    targetFolderId: item.id,
    onMoveNode,
    disabled: !isFolder,
  });

  // 获取 SaaS Logo - 从 type 或 sync_source 获取
  const syncSource = item.sync_source || getSyncSource(item.type);
  const BadgeIcon = getSyncSourceIcon(syncSource) || typeConfig.badgeIcon;

  const isPlaceholder = item.sync_status === 'not_connected';

  // Check if this item has agent access
  const hasAgentAccess = !!agentResource;
  const accessMode = agentResource?.readonly ? 'read' : 'write';

  return (
    <div
      onClick={item.onClick}
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
      style={{
        display: 'flex',
        alignItems: 'center',
        height: 36,
        padding: '0 12px',
        gap: 10,
        cursor: 'pointer',
        background: isDropTarget
          ? 'color-mix(in srgb, var(--po-accent) 15%, transparent)'
          : hasAgentAccess
          ? hovered
            ? 'color-mix(in srgb, var(--po-warning) 8%, transparent)'
            : 'color-mix(in srgb, var(--po-warning) 4%, transparent)'
          : hovered ? 'var(--po-hover)' : 'transparent',
        borderBottom: '1px solid var(--po-hover)',
        borderLeft: isDropTarget
            ? '3px solid color-mix(in srgb, var(--po-accent) 60%, transparent)'
            : hasAgentAccess
            ? '3px solid color-mix(in srgb, var(--po-warning) 60%, transparent)'
            : '3px solid transparent',
        transition: 'all 0.15s',
        // 占位符：只用透明度，hover 时恢复
        opacity: isPlaceholder ? (hovered ? 1 : 0.45) : 1,
      }}
    >
      {/* Icon: SaaS 类型显示 Logo + 格式标签，其他类型显示普通图标 */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        flexShrink: 0,
      }}>
        {BadgeIcon ? (
          // SaaS 类型：Logo + (格式) - type 决定渲染方式
          (() => {
            const isJson = typeConfig.iconCategory === 'json';
            return (
              <>
                <BadgeIcon size={16} />
                <span style={{
                  fontSize: 10,
                  fontWeight: 600,
                  color: isJson ? 'var(--po-success)' : 'var(--po-text-muted)',
                  background: isJson ? 'color-mix(in srgb, var(--po-success) 12%, transparent)' : 'var(--po-control)',
                  padding: '1px 4px',
                  borderRadius: 3,
                  textTransform: 'uppercase',
                }}>
                  {isJson ? 'JSON' : 'MD'}
                </span>
              </>
            );
          })()
        ) : (
          // 普通类型：直接显示图标
          <div>
            {getIcon(item.name, item.type)}
          </div>
        )}
      </div>

      {/* Name + Sync Status */}
      <div style={{
        flex: 1,
        fontSize: 14,
        color: hovered ? 'var(--po-text)' : 'var(--po-text-muted)',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        display: 'flex',
        alignItems: 'center',
        transition: 'color 0.15s',
      }}>
        {item.name}
        <SyncStatusIndicator status={item.sync_status} />
      </div>

      {/* Action Menu (不显示给占位符) */}
      {!isPlaceholder && (onRename || onDelete || onDuplicate || onMove || onCreateTool || (isSyncedType(item.type) && onRefresh)) && (
        <ItemActionMenu
          itemId={item.id}
          itemName={item.name}
          itemType={item.type}
          onRename={onRename}
          onDelete={onDelete}
          onDuplicate={onDuplicate}
          onMove={onMove ? (id, name) => onMove(id, name, item.version_path) : undefined}
          onRefresh={isSyncedType(item.type) ? onRefresh : undefined}
          onCreateTool={onCreateTool}
          syncUrl={item.sync_url}
          visible={hovered}
          compact
        />
      )}

      {/* 占位符状态：小圆点提示 */}
      {isPlaceholder && (
        <StatusDot status="warning" title="Click to connect" />
      )}

      {/* Read-only Lock Icon for synced items */}
      {typeConfig.isReadOnly && (
        <div style={{
          color: 'var(--po-text-disabled)',
          display: 'flex',
          alignItems: 'center',
        }}>
          <LockIcon size={10} />
        </div>
      )}

      {/* Agent Access Tag */}
      {hasAgentAccess && <AgentAccessTag mode={accessMode} />}

      {/* Chevron for folders */}
      {isFolder && (
        <div style={{ color: 'var(--po-text-disabled)', display: 'flex', alignItems: 'center' }}>
          <ChevronRightIcon />
        </div>
      )}
    </div>
  );
}

export function ListView({
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
  createLabel = 'New...',
  loading,
  agentResources,
}: ListViewProps) {
  if (loading) {
    return <PageLoading variant="fill" />;
  }

  // Create a map for quick lookup
  const resourceMap = new Map(agentResources?.map(r => [r.path, r]) ?? []);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        border: '1px solid var(--po-border)',
        borderRadius: 6,
        margin: 8,
        marginTop: 0,
        overflow: 'hidden',
      }}
    >
      {items.map(item => (
        <ListItem
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
        />
      ))}

      {items.length === 0 && (
        <div style={{
          padding: '24px 12px',
          color: 'var(--po-text-subtle)',
          fontSize: 14,
          textAlign: 'center',
        }}>
          No items yet
        </div>
      )}
    </div>
  );
}
