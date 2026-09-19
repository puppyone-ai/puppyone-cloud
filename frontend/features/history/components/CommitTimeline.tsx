'use client';

import { HISTORY_GRAPH_WIDTH, HISTORY_LINE_X, HISTORY_ROW_HEIGHT, HISTORY_ROW_ITEM_HEIGHT, HISTORY_ROW_MARGIN_Y, formatOperatorLabel, formatTimeShort, getTrackInfo, parseOperator } from '@/features/history/historyPresentation';
import {
  type VersionCommitInfo
} from '@/lib/contentTreeApi';
import { SIDEBAR_ROW_TYPOGRAPHY } from '@/lib/uiTypography';
import { useRef, useState } from 'react';

export function VerticalCommitNode({
  commit,
  hasPrevious,
  hasNext,
  isSelected,
  isHead,
  onClick,
}: {
  commit: VersionCommitInfo;
  hasPrevious: boolean;
  hasNext: boolean;
  isSelected: boolean;
  isHead: boolean;
  onClick: () => void;
}) {
  const { type, id } = parseOperator(commit.who);
  const currentInfo = getTrackInfo(commit.who);
  const actorLabel = formatOperatorLabel(type);

  const [hovered, setHovered] = useState(false);
  const rowRef = useRef<HTMLDivElement>(null);

  const trackColor = 'var(--po-filetree-rail)';
  const dotStroke = hovered ? 'var(--po-text-subtle)' : 'var(--po-text-disabled)';
  const markerSize = 6;

  return (
    <div className='workspace-history-entry' style={{ position: 'relative', height: HISTORY_ROW_HEIGHT }}>
      {/* ExplorerSidebar TreeItem Style Row */}
      <div
        ref={rowRef}
        onClick={onClick}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          display: 'flex', alignItems: 'center',
          margin: `${HISTORY_ROW_MARGIN_Y}px 6px`,
          height: HISTORY_ROW_ITEM_HEIGHT, boxSizing: 'border-box',
          borderRadius: 6,
          background: isSelected ? 'var(--po-selected)' : hovered ? 'var(--po-hover)' : 'transparent',
          color: isSelected ? 'var(--po-text)' : hovered ? 'var(--po-text)' : 'var(--po-text-muted)',
          ...SIDEBAR_ROW_TYPOGRAPHY,
          userSelect: 'none',
          transition: 'background 0.1s, color 0.1s',
          cursor: 'pointer',
          position: 'relative',
          zIndex: 10,
        }}
      >
        <div
          style={{
            flex: 1, minWidth: 0,
            display: 'flex', alignItems: 'center', height: '100%', boxSizing: 'border-box',
            paddingLeft: 6,
            paddingRight: 6,
          }}
        >
          <svg
            width={HISTORY_GRAPH_WIDTH}
            height={HISTORY_ROW_HEIGHT}
            viewBox={`0 0 ${HISTORY_GRAPH_WIDTH} ${HISTORY_ROW_HEIGHT}`}
            style={{
              flexShrink: 0,
              marginTop: -HISTORY_ROW_MARGIN_Y,
              marginBottom: -HISTORY_ROW_MARGIN_Y,
              overflow: 'visible',
              pointerEvents: 'none',
            }}
          >
            {hasPrevious && (
              <line
                x1={HISTORY_LINE_X}
                y1={0}
                x2={HISTORY_LINE_X}
                y2={HISTORY_ROW_HEIGHT / 2}
                stroke={trackColor}
                strokeWidth={1.5}
              />
            )}
            {hasNext && (
              <line
                x1={HISTORY_LINE_X}
                y1={HISTORY_ROW_HEIGHT / 2}
                x2={HISTORY_LINE_X}
                y2={HISTORY_ROW_HEIGHT}
                stroke={trackColor}
                strokeWidth={1.5}
              />
            )}
            <rect
              x={HISTORY_LINE_X - markerSize / 2}
              y={HISTORY_ROW_HEIGHT / 2 - markerSize / 2}
              width={markerSize}
              height={markerSize}
              rx={1.5}
              fill={isSelected ? currentInfo.color : hovered ? 'var(--po-panel)' : 'var(--po-canvas)'}
              stroke={isSelected ? 'none' : dotStroke}
              strokeWidth={isSelected ? 0 : 1.5}
              style={{ transition: 'fill 0.12s, stroke 0.12s' }}
            />
          </svg>

          {/* The content container starts AFTER the single line graph */}
          <div style={{
            flex: 1, minWidth: 0,
            display: 'flex', alignItems: 'center', gap: 6, height: '100%',
            paddingLeft: 4,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {isHead && (
              <span style={{
                fontSize: 9, fontWeight: 600, color: 'var(--po-success)',
                border: '1px solid color-mix(in srgb, var(--po-success) 25%, transparent)', background: 'color-mix(in srgb, var(--po-success) 12%, transparent)',
                padding: '0 4px', borderRadius: 3, display: 'inline-flex', alignItems: 'center', height: 16,
                flexShrink: 0,
              }}>
                HEAD
              </span>
            )}
            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}>
              {commit.message || `(no message)`}
            </span>

            {/* Right area actions/meta */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              justifyContent: 'flex-end', flexShrink: 0,
              marginLeft: 'auto',
            }}>
              <span
                title={id ? `${actorLabel} ${id}` : actorLabel}
                style={{
                  color: isSelected ? currentInfo.color : 'var(--po-text-subtle)',
                  fontSize: 11,
                  fontWeight: 500,
                  opacity: hovered || isSelected ? 1 : 0.75,
                  transition: 'opacity 0.2s, color 0.12s',
                }}
              >
                {actorLabel}
              </span>

              {/* Minimal Time */}
              <div style={{
                display: 'flex', alignItems: 'center', gap: 6,
                opacity: hovered || isSelected ? 1 : 0.7,
                transition: 'opacity 0.2s',
              }}>
                <span style={{ fontSize: 11, color: 'var(--po-text-subtle)', minWidth: 28, textAlign: 'right' }}>
                  {formatTimeShort(commit.created_at)}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function HistoryMoreRow({
  count,
  expanded,
  onClick,
}: {
  readonly count: number;
  readonly expanded: boolean;
  readonly onClick: () => void;
}) {
  const [hovered, setHovered] = useState(false);
  const trackColor = 'var(--po-filetree-rail)';

  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        margin: `${HISTORY_ROW_MARGIN_Y}px 6px`,
        height: HISTORY_ROW_ITEM_HEIGHT,
        width: 'calc(100% - 12px)',
        boxSizing: 'border-box',
        border: 0,
        borderRadius: 6,
        background: hovered ? 'var(--po-hover)' : 'transparent',
        color: hovered ? 'var(--po-text-muted)' : 'var(--po-text-subtle)',
        cursor: 'pointer',
        padding: '0 6px',
        textAlign: 'left',
        ...SIDEBAR_ROW_TYPOGRAPHY,
      }}
    >
      <svg
        width={HISTORY_GRAPH_WIDTH}
        height={HISTORY_ROW_HEIGHT}
        viewBox={`0 0 ${HISTORY_GRAPH_WIDTH} ${HISTORY_ROW_HEIGHT}`}
        style={{
          flexShrink: 0,
          marginTop: -HISTORY_ROW_MARGIN_Y,
          marginBottom: -HISTORY_ROW_MARGIN_Y,
          overflow: 'visible',
          pointerEvents: 'none',
        }}
      >
        <line
          x1={HISTORY_LINE_X}
          y1={0}
          x2={HISTORY_LINE_X}
          y2={HISTORY_ROW_HEIGHT / 2}
          stroke={trackColor}
          strokeWidth={1.5}
        />
        <rect
          x={HISTORY_LINE_X - 6}
          y={HISTORY_ROW_HEIGHT / 2 - 6}
          width={12}
          height={12}
          rx={2}
          fill="var(--po-canvas)"
          stroke={hovered ? 'var(--po-text-subtle)' : 'var(--po-text-disabled)'}
          strokeWidth={1.25}
        />
        <line
          x1={HISTORY_LINE_X - 3}
          y1={HISTORY_ROW_HEIGHT / 2}
          x2={HISTORY_LINE_X + 3}
          y2={HISTORY_ROW_HEIGHT / 2}
          stroke={hovered ? 'var(--po-text-muted)' : 'var(--po-text-subtle)'}
          strokeWidth={1.5}
          strokeLinecap="round"
        />
        {!expanded ? (
          <line
            x1={HISTORY_LINE_X}
            y1={HISTORY_ROW_HEIGHT / 2 - 3}
            x2={HISTORY_LINE_X}
            y2={HISTORY_ROW_HEIGHT / 2 + 3}
            stroke={hovered ? 'var(--po-text-muted)' : 'var(--po-text-subtle)'}
            strokeWidth={1.5}
            strokeLinecap="round"
          />
        ) : null}
      </svg>
      <span
        style={{
          minWidth: 0,
          paddingLeft: 4,
          fontSize: 12,
          fontWeight: 500,
        }}
      >
        {expanded ? 'Show less' : `Show ${count} more`}
      </span>
    </button>
  );
}
