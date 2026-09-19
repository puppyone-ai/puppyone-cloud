'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { Check } from 'lucide-react';
import { InlineLoading } from '@/components/loading';
import { TreeDisclosureMarker } from '@/components/ui/TreeDisclosureMarker';
import { useContentNodes } from '@/lib/hooks/useData';
import { sortNodes, type NodeInfo } from '@/lib/contentTreeApi';
import { FileGlyphIcon } from '@/lib/fileIcons';
import { T } from '@/features/access/lib/tokens';

const ROW_HEIGHT = 30;
const ROW_GAP = 2;
const ROW_MARGIN_X = 6;
const ROW_MARGIN_Y = ROW_GAP / 2;
const TREE_INDENT = 16;
const CONTENT_INSET = 8;
const LINE_OVERDRAW = 2;
const LINE_HEIGHT = ROW_HEIGHT + LINE_OVERDRAW * 2;
const HOOK_Y = LINE_OVERDRAW + ROW_HEIGHT / 2;
const STATUS_WIDTH = 100;
const ACCESS_TREE_TYPE = {
  body: 13,
  meta: 12,
  label: 11,
} as const;

export function FolderAccessTree({
  projectId,
  selectedPath,
  existingPathSet,
  initialExpandedPath,
  onSelect,
}: {
  readonly projectId: string;
  readonly selectedPath: string | null;
  readonly existingPathSet: ReadonlySet<string>;
  readonly initialExpandedPath?: string | null;
  readonly onSelect: (path: string) => void;
}) {
  const [expandedPaths, setExpandedPaths] = useState<ReadonlySet<string>>(
    () => new Set(['', ...ancestorPaths(initialExpandedPath ?? '')]),
  );

  useEffect(() => {
    if (!selectedPath) return;
    setExpandedPaths((current) => {
      const next = new Set(current);
      next.add('');
      ancestorPaths(selectedPath).forEach((path) => next.add(path));
      return next;
    });
  }, [selectedPath]);

  const isExpanded = useCallback(
    (path: string) => expandedPaths.has(normalizePath(path)),
    [expandedPaths],
  );
  const toggleExpanded = useCallback((path: string) => {
    const normalized = normalizePath(path);
    setExpandedPaths((current) => {
      const next = new Set(current);
      if (next.has(normalized)) next.delete(normalized);
      else next.add(normalized);
      next.add('');
      return next;
    });
  }, []);

  return (
    <div
      style={{
        minWidth: 0,
        borderRadius: 8,
        border: `1px solid ${T.cardBorder}`,
        background: T.cardBg,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          padding: '10px 12px 8px',
          borderBottom: `1px solid ${T.cardBorder}`,
          color: 'var(--po-text-subtle)',
          fontSize: ACCESS_TREE_TYPE.label,
          lineHeight: '14px',
          fontWeight: 600,
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
          fontFamily: T.fontSans,
        }}
      >
        Choose from Files
      </div>
      <div style={{ height: 368, overflow: 'auto', padding: '6px 0 10px' }}>
        <TreeRootRow />
        <FolderChildren
          projectId={projectId}
          parentPath=""
          depth={1}
          ancestorLastSiblings={[]}
          selectedPath={selectedPath}
          existingPathSet={existingPathSet}
          isExpanded={isExpanded}
          onToggle={toggleExpanded}
          onSelect={onSelect}
        />
      </div>
    </div>
  );
}

function TreeRootRow() {
  return (
    <div
      style={{
        height: ROW_HEIGHT,
        margin: `${ROW_MARGIN_Y}px ${ROW_MARGIN_X}px`,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: `0 8px 0 ${CONTENT_INSET}px`,
        borderRadius: 6,
        color: T.text2,
        fontFamily: T.fontSans,
        fontSize: ACCESS_TREE_TYPE.body,
      }}
    >
      <span style={{ width: 18, height: 18, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
        <AccessFolderIcon expanded />
      </span>
      <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 500 }}>
        Root
      </span>
      <AccessStatusText />
    </div>
  );
}

function FolderChildren({
  projectId,
  parentPath,
  depth,
  ancestorLastSiblings,
  selectedPath,
  existingPathSet,
  isExpanded,
  onToggle,
  onSelect,
}: {
  readonly projectId: string;
  readonly parentPath: string;
  readonly depth: number;
  readonly ancestorLastSiblings: readonly boolean[];
  readonly selectedPath: string | null;
  readonly existingPathSet: ReadonlySet<string>;
  readonly isExpanded: (path: string) => boolean;
  readonly onToggle: (path: string) => void;
  readonly onSelect: (path: string) => void;
}) {
  const { nodes, isLoading, error } = useContentNodes(projectId, parentPath);
  const entries = useMemo(() => sortNodes(nodes), [nodes]);

  if (isLoading) return <TreeMessage depth={depth}><InlineLoading label="Loading" size="xs" /></TreeMessage>;
  if (error) return <TreeMessage depth={depth}>Could not load this folder.</TreeMessage>;
  if (entries.length === 0) return <TreeMessage depth={depth}>Empty folder</TreeMessage>;

  return (
    <>
      {entries.map((entry, index) => {
        const normalizedPath = normalizePath(entry.path);
        const isLastSibling = index === entries.length - 1;
        if (entry.type !== 'folder') {
          return (
            <FileRow
              key={entry.path}
              entry={entry}
              depth={depth}
              isLastSibling={isLastSibling}
              ancestorLastSiblings={ancestorLastSiblings}
            />
          );
        }

        const expanded = isExpanded(normalizedPath);
        return (
          <div key={entry.path}>
            <FolderRow
              entry={entry}
              depth={depth}
              isLastSibling={isLastSibling}
              ancestorLastSiblings={ancestorLastSiblings}
              expanded={expanded}
              selected={selectedPath === normalizedPath}
              alreadyExists={existingPathSet.has(normalizedPath)}
              onToggle={() => onToggle(normalizedPath)}
              onSelect={() => onSelect(normalizedPath)}
            />
            {expanded ? (
              <FolderChildren
                projectId={projectId}
                parentPath={normalizedPath}
                depth={depth + 1}
                ancestorLastSiblings={[...ancestorLastSiblings, isLastSibling]}
                selectedPath={selectedPath}
                existingPathSet={existingPathSet}
                isExpanded={isExpanded}
                onToggle={onToggle}
                onSelect={onSelect}
              />
            ) : null}
          </div>
        );
      })}
    </>
  );
}

function FolderRow({
  entry,
  depth,
  isLastSibling,
  ancestorLastSiblings,
  expanded,
  selected,
  alreadyExists,
  onToggle,
  onSelect,
}: {
  readonly entry: NodeInfo;
  readonly depth: number;
  readonly isLastSibling: boolean;
  readonly ancestorLastSiblings: readonly boolean[];
  readonly expanded: boolean;
  readonly selected: boolean;
  readonly alreadyExists: boolean;
  readonly onToggle: () => void;
  readonly onSelect: () => void;
}) {
  const [hovered, setHovered] = useState(false);

  return (
    <div
      style={{
        position: 'relative',
        height: ROW_HEIGHT,
        margin: `${ROW_MARGIN_Y}px ${ROW_MARGIN_X}px`,
      }}
    >
      <TreeGuides
        depth={depth}
        isLastSibling={isLastSibling}
        ancestorLastSiblings={ancestorLastSiblings}
      />
      <div
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(event) => {
          if (event.key !== 'Enter' && event.key !== ' ') return;
          event.preventDefault();
          onToggle();
        }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        title={`${expanded ? 'Collapse' : 'Expand'} ${formatPath(entry.path)}`}
        style={{
          position: 'relative',
          zIndex: 1,
          width: '100%',
          height: ROW_HEIGHT,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          border: 'none',
          borderRadius: 6,
          background: selected
            ? 'var(--po-selected)'
            : hovered
              ? 'var(--po-hover)'
              : 'transparent',
          color: selected ? T.text1 : T.text2,
          padding: `0 8px 0 ${CONTENT_INSET + depth * TREE_INDENT}px`,
          cursor: 'pointer',
          textAlign: 'left',
          fontFamily: T.fontSans,
          fontSize: ACCESS_TREE_TYPE.body,
          transition: 'background 0.12s ease, color 0.12s ease',
        }}
      >
        <span
          aria-hidden
          style={{
            width: 18,
            height: 18,
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          <AccessFolderIcon expanded={expanded} />
        </span>
        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: selected ? 500 : 400 }}>
          {entry.name}
        </span>
        <FolderRowStatus
          selected={selected}
          alreadyExists={alreadyExists}
          onSelect={onSelect}
        />
      </div>
    </div>
  );
}

function FileRow({
  entry,
  depth,
  isLastSibling,
  ancestorLastSiblings,
}: {
  readonly entry: NodeInfo;
  readonly depth: number;
  readonly isLastSibling: boolean;
  readonly ancestorLastSiblings: readonly boolean[];
}) {
  return (
    <div
      title={formatPath(entry.path)}
      aria-disabled="true"
      style={{
        position: 'relative',
        height: ROW_HEIGHT,
        margin: `${ROW_MARGIN_Y}px ${ROW_MARGIN_X}px`,
        display: 'flex',
        alignItems: 'center',
        padding: `0 8px 0 ${CONTENT_INSET + depth * TREE_INDENT}px`,
        color: T.text4,
        fontFamily: T.fontSans,
        fontSize: ACCESS_TREE_TYPE.meta,
        opacity: 0.82,
        boxSizing: 'border-box',
      }}
    >
      <TreeGuides
        depth={depth}
        isLastSibling={isLastSibling}
        ancestorLastSiblings={ancestorLastSiblings}
      />
      <div
        style={{
          position: 'relative',
          zIndex: 1,
          minWidth: 0,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          width: '100%',
        }}
      >
        <span aria-hidden style={{ width: 18, height: 18, flexShrink: 0 }} />
        <FileGlyphIcon name={entry.name} type={entry.type} size={16} />
        <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {entry.name}
        </span>
      </div>
    </div>
  );
}

function TreeMessage({
  depth,
  children,
}: {
  readonly depth: number;
  readonly children: ReactNode;
}) {
  return (
    <div
      style={{
        minHeight: 28,
        display: 'flex',
        alignItems: 'center',
        paddingLeft: CONTENT_INSET + depth * TREE_INDENT + 24,
        color: T.text4,
        fontSize: ACCESS_TREE_TYPE.meta,
        fontFamily: T.fontSans,
      }}
    >
      {children}
    </div>
  );
}

function ancestorPaths(path: string): string[] {
  const parts = normalizePath(path).split('/').filter(Boolean);
  const ancestors: string[] = [];
  for (let index = 1; index < parts.length; index++) {
    ancestors.push(parts.slice(0, index).join('/'));
  }
  return ancestors;
}

function normalizePath(path: string): string {
  return path.trim().replace(/^\/+|\/+$/g, '').replace(/\/+/g, '/');
}

function formatPath(path: string): string {
  return path === '' ? 'Project files' : `/${path}`;
}

function FolderRowStatus({
  selected,
  alreadyExists,
  onSelect,
}: {
  readonly selected: boolean;
  readonly alreadyExists: boolean;
  readonly onSelect: () => void;
}) {
  if (alreadyExists) {
    return <AccessStatusText onSelect={onSelect} />;
  }

  return (
    <button
      type="button"
      title={selected ? 'Selected folder' : 'Select this folder'}
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
      style={{
        width: STATUS_WIDTH,
        display: 'inline-flex',
        justifyContent: 'flex-end',
        alignItems: 'center',
        gap: 6,
        flexShrink: 0,
        fontSize: ACCESS_TREE_TYPE.meta,
        fontWeight: 500,
        border: 0,
        background: 'transparent',
        color: selected ? 'var(--po-accent)' : T.text3,
        cursor: 'pointer',
        transition: 'opacity 0.12s ease, color 0.12s ease',
      }}
    >
      <span>{selected ? 'Selected' : 'Select'}</span>
      <span
        aria-hidden
        style={{
          width: 20,
          height: 20,
          borderRadius: 5,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: selected ? 'var(--po-accent)' : 'transparent',
          border: selected
            ? '1px solid var(--po-accent)'
            : `1px solid ${T.border}`,
          color: selected ? 'white' : 'transparent',
        }}
      >
        {selected ? <Check size={13} strokeWidth={2.6} /> : null}
      </span>
    </button>
  );
}

function AccessStatusText({ onSelect }: { readonly onSelect?: () => void }) {
  const contentStyle = {
    width: STATUS_WIDTH,
    flexShrink: 0,
    textAlign: 'right' as const,
    color: T.text4,
    fontSize: ACCESS_TREE_TYPE.meta,
    fontWeight: 500,
  };

  if (!onSelect) {
    return (
      <span title="This folder already has access" style={contentStyle}>
        Has access
      </span>
    );
  }

  return (
    <button
      type="button"
      title="Open this access"
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
      style={{
        ...contentStyle,
        padding: 0,
        border: 0,
        background: 'transparent',
        cursor: 'pointer',
        font: 'inherit',
        lineHeight: 'inherit',
      }}
    >
      Has access
    </button>
  );
}

function TreeGuides({
  depth,
  isLastSibling,
  ancestorLastSiblings,
}: {
  readonly depth: number;
  readonly isLastSibling: boolean;
  readonly ancestorLastSiblings: readonly boolean[];
}) {
  if (depth <= 0) return null;
  const width = CONTENT_INSET + depth * TREE_INDENT + 8;

  return (
    <svg
      width={width}
      height={LINE_HEIGHT}
      viewBox={`0 0 ${width} ${LINE_HEIGHT}`}
      shapeRendering="crispEdges"
      aria-hidden
      style={{
        position: 'absolute',
        left: 0,
        top: -LINE_OVERDRAW,
        pointerEvents: 'none',
        zIndex: 0,
      }}
    >
      {ancestorLastSiblings.map((last, index) => {
        if (last) return null;
        const level = index + 1;
        return (
          <rect
            key={level}
            x={level * TREE_INDENT}
            y={0}
            width={1}
            height={LINE_HEIGHT}
            fill="var(--po-tree-guide)"
          />
        );
      })}
      <rect
        x={depth * TREE_INDENT}
        y={0}
        width={1}
        height={isLastSibling ? HOOK_Y : LINE_HEIGHT}
        fill="var(--po-tree-guide)"
      />
      <rect
        x={depth * TREE_INDENT}
        y={HOOK_Y}
        width={8}
        height={1}
        fill="var(--po-tree-guide)"
      />
    </svg>
  );
}

function AccessFolderIcon({ expanded = false }: { readonly expanded?: boolean }) {
  return <TreeDisclosureMarker expanded={expanded} />;
}
