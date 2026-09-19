'use client';

import { PageLoading } from '@/components/loading';
import type { DiffLine } from '@/features/history/diffModel';
import { HISTORY_DIFF_HEADER_BG, fileExtClass, formatFullTime, formatOperatorLabel, formatTime, parseOperator } from '@/features/history/historyPresentation';
import { useDiffPreview } from '@/features/history/useDiffPreview';
import {
  getVersionContent,
  type VersionCommitChange,
  type VersionCommitInfo
} from '@/lib/contentTreeApi';
import { PROJECT_CONTENT_RAIL_WIDTH } from '@/lib/layout';
import { ChevronRight } from 'lucide-react';
import { useMemo, useState } from 'react';
import useSWR from 'swr';

export const OP_TONE: Record<string, { bg: string; fg: string }> = {
  added:    { bg: 'color-mix(in srgb, var(--po-success) 15%, transparent)',  fg: 'var(--po-success)' },
  modified: { bg: 'color-mix(in srgb, var(--po-accent) 15%, transparent)', fg: 'var(--po-accent)' },
  deleted:  { bg: 'color-mix(in srgb, var(--po-danger) 15%, transparent)',  fg: 'var(--po-danger)' },
};

export function DiffRow({ line }: { line: DiffLine }) {
  if (line.kind === 'hunk') {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          minWidth: 0,
          width: '100%',
          height: 24,
          paddingLeft: 14,
          background: HISTORY_DIFF_HEADER_BG,
          color: 'var(--po-text-disabled)',
          fontFamily: 'var(--po-font-sans)',
          fontSize: 11,
          borderTop: '1px solid var(--po-hover)',
          borderBottom: '1px solid var(--po-hover)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {line.text}
      </div>
    );
  }

  const isAdd = line.kind === 'add';
  const isRem = line.kind === 'remove';
  const bg = isAdd
    ? 'var(--po-diff-added-bg)'
    : isRem
    ? 'var(--po-diff-removed-bg)'
    : 'transparent';
  const numColor = isAdd ? 'var(--po-success)' : isRem ? 'var(--po-danger)' : 'var(--po-text-disabled)';
  const prefix = isAdd ? '+' : isRem ? '-' : ' ';
  const textColor = isAdd ? 'var(--po-diff-added-text)' : isRem ? 'var(--po-diff-removed-text)' : 'var(--po-text-muted)';
  const lineNum = isRem ? line.oldLine : line.newLine ?? line.oldLine;
  return (
    <div
      style={{
        display: 'flex',
        minWidth: 0,
        width: '100%',
        background: bg,
        fontFamily: 'var(--po-font-sans)',
        fontSize: 11.5,
        lineHeight: '20px',
        minHeight: 20,
      }}
    >
      <span
        style={{
          width: 44,
          textAlign: 'right',
          paddingRight: 10,
          color: numColor,
          opacity: 0.7,
          flexShrink: 0,
          userSelect: 'none',
        }}
      >
        {lineNum ?? ''}
      </span>
      <span
        style={{
          width: 14,
          textAlign: 'center',
          color: numColor,
          flexShrink: 0,
          fontWeight: 600,
        }}
      >
        {prefix}
      </span>
      <span
        style={{
          flex: 1,
          minWidth: 0,
          color: textColor,
          whiteSpace: 'pre-wrap',
          overflowWrap: 'anywhere',
          wordBreak: 'break-word',
          paddingLeft: 4,
          paddingRight: 12,
        }}
      >
        {line.text}
      </span>
    </div>
  );
}

export interface FileDiffBlockProps {
  change: VersionCommitChange;
  projectId: string;
  commitId: string;
  parentCommitId: string | null;
  defaultExpanded?: boolean;
}

export function FileDiffBlock({
  change,
  projectId,
  commitId,
  parentCommitId,
  defaultExpanded = false,
}: FileDiffBlockProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const op = change.op;
  const tone = OP_TONE[op] ?? OP_TONE.modified;
  const ext = fileExtClass(change.path);

  // Fetch current content for added/modified; previous content for
  // modified/deleted. SWR keys are scoped per (path, commit) so a
  // parent fetch can be reused across rows.
  const needsCurrent = op === 'added' || op === 'modified';
  const needsParent = (op === 'modified' || op === 'deleted') && !!parentCommitId;

  const { data: currentDetail, error: currentErr } = useSWR(
    expanded && needsCurrent ? ['version-preview', projectId, change.path, commitId] : null,
    () => getVersionContent(change.path, commitId, projectId, { previewBytes: 256_000 }),
    { revalidateOnFocus: false, dedupingInterval: 60000 },
  );
  const { data: parentDetail, error: parentErr } = useSWR(
    expanded && needsParent ? ['version-preview', projectId, change.path, parentCommitId] : null,
    () => getVersionContent(change.path, parentCommitId!, projectId, { previewBytes: 256_000 }),
    { revalidateOnFocus: false, dedupingInterval: 60000 },
  );

  const isFetching =
    (needsCurrent && !currentDetail && !currentErr) ||
    (needsParent && !parentDetail && !parentErr);
  const request = useMemo(() => expanded && !isFetching && !currentErr && !parentErr
    ? { op, current: currentDetail, previous: parentDetail } : null,
    [expanded, isFetching, currentErr, parentErr, op, currentDetail, parentDetail]);
  const preview = useDiffPreview(request);
  const isLoading = isFetching || Boolean(request && !preview);
  const lines = preview?.lines ?? null;
  const placeholder = currentErr || parentErr ? 'Failed to load diff' : preview?.placeholder ?? null;

  return (
    <div
      style={{
        width: '100%',
        minWidth: 0,
        marginBottom: 16,
        borderRadius: 8,
        overflow: 'hidden',
        border: '1px solid var(--po-border-subtle)',
      }}
    >
      {/* File header */}
      <button
        className='workspace-history-file'
        type='button'
        aria-expanded={expanded}
        onClick={() => setExpanded(value => !value)}
        style={{
          width: '100%',
          height: 32,
          padding: '0 12px',
          display: 'flex',
          alignItems: 'center',
          minWidth: 0,
          gap: 8,
          background: HISTORY_DIFF_HEADER_BG,
          border: 0,
          borderBottom: expanded ? '1px solid var(--po-border-subtle)' : 'none',
          color: 'inherit',
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <ChevronRight
          size={13}
          strokeWidth={1.8}
          aria-hidden='true'
          style={{
            flexShrink: 0,
            color: 'var(--po-text-subtle)',
            transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
            transition: 'transform 120ms ease',
          }}
        />
        <svg
          width='14'
          height='14'
          viewBox='0 0 24 24'
          fill='none'
          stroke={ext === 'json' ? 'var(--po-success)' : ext === 'markdown' ? 'var(--po-text-muted)' : 'var(--po-text-subtle)'}
          strokeWidth='1.5'
          strokeLinecap='round'
          strokeLinejoin='round'
          style={{ flexShrink: 0 }}
        >
          <path d='M4 4v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6H6a2 2 0 0 0-2 2z' />
          <path d='M14 2v6h6' />
        </svg>
        <span
          style={{
            fontSize: 11.5,
            color: 'var(--po-text-muted)',
            fontFamily: 'var(--po-font-sans)',
            flex: 1,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {change.path}
        </span>
        <span
          style={{
            padding: '1px 6px',
            fontSize: 9.5,
            fontWeight: 600,
            borderRadius: 3,
            fontFamily: 'var(--po-font-sans)',
            letterSpacing: 0,
            textTransform: 'uppercase',
            background: tone.bg,
            color: tone.fg,
          }}
        >
          {op}
        </span>
      </button>

      {/* Diff body */}
      {!expanded ? null : isLoading ? (
        <div
          style={{
            height: 56,
            display: 'flex',
            background: 'var(--po-inset)',
          }}
        >
          <PageLoading variant="fill" label="Loading diff" />
        </div>
      ) : placeholder ? (
        <div
          style={{
            padding: '14px 16px',
            fontSize: 11,
            color: 'var(--po-text-subtle)',
            background: 'var(--po-inset)',
            fontFamily: 'var(--po-font-sans)',
            fontStyle: 'italic',
          }}
        >
          {placeholder}
        </div>
      ) : lines && lines.length > 0 ? (
        <div style={{ minWidth: 0, width: '100%', padding: '6px 0', background: 'var(--po-inset)' }}>
          {lines.map((line, j) => (
            <DiffRow key={j} line={line} />
          ))}
        </div>
      ) : (
        <div
          style={{
            padding: '14px 16px',
            fontSize: 11,
            color: 'var(--po-text-subtle)',
            background: 'var(--po-inset)',
            fontStyle: 'italic',
          }}
        >
          No textual changes
        </div>
      )}
    </div>
  );
}

export function CommitDetail({
  commit,
  projectId,
  parentCommitId,
}: {
  commit: VersionCommitInfo;
  projectId: string;
  parentCommitId: string | null;
}) {
  const { type, id } = parseOperator(commit.who);
  const opColors: Record<string, { background: string; color: string; borderColor: string }> = {
    user: {
      background: 'color-mix(in srgb, var(--po-accent) 10%, transparent)',
      color: 'var(--po-accent)',
      borderColor: 'color-mix(in srgb, var(--po-accent) 20%, transparent)',
    },
    agent: {
      background: 'color-mix(in srgb, var(--po-purple) 10%, transparent)',
      color: 'var(--po-purple)',
      borderColor: 'color-mix(in srgb, var(--po-purple) 20%, transparent)',
    },
    sync: {
      background: 'color-mix(in srgb, var(--po-success) 10%, transparent)',
      color: 'var(--po-success)',
      borderColor: 'color-mix(in srgb, var(--po-success) 20%, transparent)',
    },
    system: {
      background: 'var(--po-control)',
      color: 'var(--po-text-muted)',
      borderColor: 'var(--po-border)',
    },
  };
  const opColor = opColors[type] || opColors.system;

  return (
    <div
      className="p-6 md:p-8 mx-auto"
      style={{
        width: '100%',
        maxWidth: PROJECT_CONTENT_RAIL_WIDTH,
        boxSizing: 'border-box',
      }}
    >
      <div className="flex flex-wrap items-center gap-4 mb-6">
        <div className="flex items-center gap-3">
          <span
            className="text-lg font-medium text-[var(--po-text)] font-sans"
            title={commit.commit_id}
          >
            {commit.commit_id.slice(0, 8)}
          </span>
          <span className="text-sm text-[var(--po-text-muted)]">
            {commit.message || '(no message)'}
          </span>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-3">
          <span
            className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md border font-medium"
            style={opColor}
          >
            {formatOperatorLabel(type)}
            {id && <span className="opacity-70 font-normal font-sans">{id.slice(0, 8)}</span>}
          </span>

          <span
            className="text-xs text-[var(--po-text-subtle)] font-medium"
            title={formatFullTime(commit.created_at)}
          >
            {formatTime(commit.created_at)}
          </span>

          {commit.root_hash && (
            <span className="text-xs text-[var(--po-text-subtle)] font-sans bg-[var(--po-control)] px-2 py-1 rounded border border-[var(--po-border-subtle)]">
              {commit.root_hash.slice(0, 10)}
            </span>
          )}
        </div>
      </div>

      {commit.changes.length > 0 ? (
        <>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              fontSize: 11,
              fontFamily: 'var(--po-font-sans)',
              color: 'var(--po-text-subtle)',
              marginBottom: 16,
              paddingBottom: 14,
              borderBottom: '1px solid var(--po-border-subtle)',
            }}
          >
            <span>
              {commit.changes.length} file
              {commit.changes.length !== 1 ? 's' : ''} changed
            </span>
          </div>
          {commit.changes.map((change, i) => (
            <FileDiffBlock
              key={`${change.path}-${i}`}
              change={change}
              projectId={projectId}
              commitId={commit.commit_id}
              parentCommitId={parentCommitId}
              defaultExpanded={i === 0}
            />
          ))}
        </>
      ) : (
        <div className="px-6 py-12 text-center text-[var(--po-text-subtle)] text-sm border border-[var(--po-border-subtle)] rounded-xl">
          No file changes in this commit
        </div>
      )}

      {commit.conflicts.length > 0 && (
        <div
          className="mt-4 rounded-xl border p-4"
          style={{
            background: 'color-mix(in srgb, var(--po-warning) 3%, transparent)',
            borderColor: 'color-mix(in srgb, var(--po-warning) 22%, transparent)',
          }}
        >
          <div className="text-xs font-medium text-[var(--po-warning)] mb-3 flex items-center gap-2">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
            Merge Conflicts ({commit.conflicts.length})
          </div>
          <div className="space-y-1.5">
            {commit.conflicts.map((conflict, i) => (
              <div key={i} className="text-xs font-sans text-[var(--po-text-muted)] flex items-center gap-2 bg-[var(--po-inset)] p-2 rounded border border-[var(--po-border-subtle)]">
                <span className="text-[var(--po-text)]">{conflict.path}</span>
                <span className="text-[var(--po-text-subtle)]">-</span>
                <span style={{ color: 'color-mix(in srgb, var(--po-warning) 80%, var(--po-text-muted))' }}>{conflict.strategy}</span>
                {conflict.kept && <span className="text-[var(--po-text-subtle)]">(kept: {conflict.kept})</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
