'use client';

import React, { useState, useCallback, useMemo } from 'react';
import useSWR from 'swr';
import {
  getVersionHistory,
  rollbackToVersion,
  type FileVersionInfo,
  type VersionCommitChange,
} from '@/lib/contentTreeApi';
import { PageLoading } from '@/components/loading';
import { ActivityIconButton } from '@/components/ActivityIconButton';
import { ActionButton } from '@/components/ui/ActionButton';

interface VersionHistoryPanelProps {
  nodeId: string;  // File path in the project tree.
  projectId: string;
  onClose: () => void;
  onRollbackComplete?: () => void;
}

const OP_COLORS: Record<string, string> = {
  added: 'var(--po-success)',
  modified: 'var(--po-accent)',
  deleted: 'var(--po-danger)',
};

function formatTime(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString(undefined, {
    month: 'short', day: 'numeric',
    year: d.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
  });
}

function formatFullTime(iso: string | null): string {
  if (!iso) return '';
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function parseOperator(who: string): { type: string; id: string } {
  if (who.includes(':')) {
    const [type, ...rest] = who.split(':');
    return { type, id: rest.join(':') };
  }
  return { type: who || 'system', id: '' };
}

function OperatorBadge({ who }: { who: string }) {
  const { type, id } = parseOperator(who);
  const colors: Record<string, { bg: string; text: string }> = {
    user: { bg: 'color-mix(in srgb, var(--po-accent) 15%, transparent)', text: 'var(--po-accent)' },
    agent: { bg: 'color-mix(in srgb, var(--po-file-accent-audio) 15%, transparent)', text: 'var(--po-file-accent-audio)' },
    sync: { bg: 'color-mix(in srgb, var(--po-success) 15%, transparent)', text: 'var(--po-success)' },
    system: { bg: 'color-mix(in srgb, var(--po-text-muted) 15%, transparent)', text: 'var(--po-text-muted)' },
  };
  const c = colors[type] || colors.system;
  const label = type.charAt(0).toUpperCase() + type.slice(1);
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      fontSize: 11, padding: '2px 8px', borderRadius: 4,
      background: c.bg, color: c.text, fontWeight: 500,
    }}>
      {label}
      {id && <span style={{ opacity: 0.7, fontWeight: 400 }}>{id.slice(0, 8)}</span>}
    </span>
  );
}

function ChangeSummary({ changes }: { changes: VersionCommitChange[] }) {
  const summary = useMemo(() => {
    const counts = { added: 0, modified: 0, deleted: 0 };
    for (const c of changes) {
      counts[c.op]++;
    }
    return counts;
  }, [changes]);

  return (
    <div style={{ display: 'flex', gap: 8, fontSize: 11 }}>
      {summary.added > 0 && <span style={{ color: OP_COLORS.added }}>+{summary.added}</span>}
      {summary.modified > 0 && <span style={{ color: OP_COLORS.modified }}>~{summary.modified}</span>}
      {summary.deleted > 0 && <span style={{ color: OP_COLORS.deleted }}>-{summary.deleted}</span>}
    </div>
  );
}

function shortCommit(cid: string): string {
  return cid ? cid.slice(0, 8) : '';
}

function CommitRow({
  commit,
  isCurrent,
  onRollback,
}: {
  commit: FileVersionInfo;
  isCurrent: boolean;
  onRollback: (commitId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const shortId = shortCommit(commit.commit_id);

  return (
    <div style={{ position: 'relative', paddingLeft: 24 }}>
      <div style={{
        position: 'absolute', left: 7, top: 16,
        width: 6, height: 6, borderRadius: 1.5,
        background: isCurrent ? 'var(--po-success)' : 'var(--po-filetree-rail)',
        border: `1px solid ${isCurrent ? 'var(--po-success)' : 'var(--po-text-disabled)'}`,
        boxSizing: 'border-box',
        zIndex: 1,
      }} />

      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          padding: '10px 12px',
          borderRadius: 6,
          border: '1px solid var(--po-border-subtle)',
          background: expanded ? 'var(--po-hover)' : 'transparent',
          cursor: 'pointer',
          transition: 'background 0.1s',
          marginBottom: 2,
        }}
        onMouseEnter={e => { if (!expanded) e.currentTarget.style.background = 'var(--po-hover)'; }}
        onMouseLeave={e => { if (!expanded) e.currentTarget.style.background = 'transparent'; }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
            <span
              title={commit.commit_id}
              style={{
                fontFamily: 'var(--po-font-sans)',
                fontSize: 11, fontWeight: 600,
                color: isCurrent ? 'var(--po-success)' : 'var(--po-text)',
                flexShrink: 0,
              }}
            >
              {shortId}
            </span>
            {isCurrent && (
              <span style={{
                fontSize: 9, padding: '1px 5px', borderRadius: 3,
                background: 'color-mix(in srgb, var(--po-success) 15%, transparent)', color: 'var(--po-success)', fontWeight: 500,
              }}>
                HEAD
              </span>
            )}
            <span style={{
              fontSize: 12, color: 'var(--po-text-muted)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {commit.message || '(no message)'}
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            <ChangeSummary changes={commit.changes} />
            <span style={{ fontSize: 10, color: 'var(--po-text-disabled)', whiteSpace: 'nowrap' }} title={formatFullTime(commit.created_at)}>
              {formatTime(commit.created_at)}
            </span>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <OperatorBadge who={commit.who} />
          {commit.root_hash && (
            <span style={{
              fontSize: 10, color: 'var(--po-text-disabled)', marginLeft: 'auto',
              fontFamily: 'var(--po-font-sans)',
            }}>
              {commit.root_hash.slice(0, 12)}
            </span>
          )}
        </div>

        {expanded && commit.changes.length > 0 && (
          <div style={{
            marginTop: 10, paddingTop: 10,
            borderTop: '1px solid var(--po-border-subtle)',
          }}>
            <div style={{ fontSize: 10, color: 'var(--po-text-subtle)', marginBottom: 6 }}>
              {commit.changes.length} file{commit.changes.length !== 1 ? 's' : ''} changed
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {commit.changes.map((change, i) => {
                const op = change.op;
                return (
                  <div key={i} style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    padding: '3px 6px', borderRadius: 4, fontSize: 11,
                  }}>
                    <span style={{
                      width: 6, height: 6, borderRadius: 2,
                      background: OP_COLORS[op] || 'var(--po-text-subtle)',
                      flexShrink: 0,
                    }} />
                    <span style={{
                      fontFamily: 'var(--po-font-sans)',
                      color: 'var(--po-text-muted)',
                    }}>
                      {change.path}
                    </span>
                    <span style={{
                      fontSize: 9, color: OP_COLORS[op] || 'var(--po-text-subtle)',
                      marginLeft: 'auto', flexShrink: 0,
                    }}>
                      {op}
                    </span>
                  </div>
                );
              })}
            </div>

            <div style={{ display: 'flex', gap: 6, marginTop: 8 }} onClick={e => e.stopPropagation()}>
              {!isCurrent && (
                <button
                  onClick={() => onRollback(commit.commit_id)}
                  style={{
                    fontSize: 10, height: 30, padding: '0 10px', borderRadius: 4,
                    border: '1px solid color-mix(in srgb, var(--po-warning) 30%, transparent)',
                    background: 'color-mix(in srgb, var(--po-warning) 10%, transparent)',
                    color: 'var(--po-warning)', cursor: 'pointer',
                  }}
                >
                  Rollback to {shortId}
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function VersionHistoryPanel({
  nodeId,
  projectId,
  onClose,
  onRollbackComplete,
}: VersionHistoryPanelProps) {
  const [rollbackConfirm, setRollbackConfirm] = useState<string | null>(null);
  const [isRollingBack, setIsRollingBack] = useState(false);
  const [rollbackError, setRollbackError] = useState<string | null>(null);

  const { data: history, error: historyError, mutate: refreshHistory } = useSWR(
    nodeId ? ['version-history', nodeId, projectId] : null,
    () => getVersionHistory(nodeId, projectId),
    { revalidateOnFocus: false },
  );

  const handleRollback = useCallback(async (commitId: string) => {
    setIsRollingBack(true);
    setRollbackError(null);
    try {
      await rollbackToVersion(commitId, projectId);
      setRollbackConfirm(null);
      await refreshHistory();
      onRollbackComplete?.();
    } catch (err) {
      // Keep the confirm dialog open and show the failure instead of
      // silently swallowing it (previously console.error only).
      console.error('Rollback failed:', err);
      setRollbackError(err instanceof Error ? err.message : 'Rollback failed. Please try again.');
    } finally {
      setIsRollingBack(false);
    }
  }, [projectId, refreshHistory, onRollbackComplete]);

  const commits = history?.commits ?? [];
  const headCommitId = history?.head_commit_id ?? '';

  const fileName = nodeId.includes('/') ? nodeId.split('/').pop() : nodeId;

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      background: 'var(--po-canvas)',
      color: 'var(--po-text)',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '10px 16px',
        borderBottom: '1px solid var(--po-filetree-rail)',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--po-text-muted)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          <span style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {fileName}
          </span>
          {history && (
            <span style={{ fontSize: 11, color: 'var(--po-text-disabled)', flexShrink: 0 }}>
              {history.total} commit{history.total !== 1 ? 's' : ''}
            </span>
          )}
        </div>
        <ActivityIconButton kind="close" title="Close panel" onClick={onClose} />
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 8px' }}>
        {historyError && (
          <div style={{ padding: 24, textAlign: 'center', color: 'var(--po-danger)', fontSize: 13 }}>
            Failed to load version history
          </div>
        )}

        {!history && !historyError && (
          <div style={{ height: 120, display: 'flex' }}>
            <PageLoading variant="fill" />
          </div>
        )}

        {commits.length > 0 && (
          <div style={{ position: 'relative' }}>
            <div style={{
              position: 'absolute', left: 10, top: 20, bottom: 20,
              width: 2, background: 'var(--po-overlay)',
            }} />
            {commits.map((commit) => (
              <CommitRow
                key={commit.commit_id}
                commit={commit}
                isCurrent={Boolean(headCommitId) && commit.commit_id === headCommitId}
                onRollback={(cid) => { setRollbackConfirm(cid); setRollbackError(null); }}
              />
            ))}
          </div>
        )}

        {commits.length === 0 && history && (
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            height: 200, gap: 8, color: 'var(--po-text-disabled)',
          }}>
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.5 }}>
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            <span style={{ fontSize: 13 }}>No commits for this file</span>
          </div>
        )}
      </div>

      {/* Rollback Confirmation */}
      {rollbackConfirm !== null && (
        <div style={{
          position: 'absolute', inset: 0,
          background: 'var(--po-backdrop)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 100, backdropFilter: 'blur(4px)',
        }}>
          <div style={{
            background: 'var(--po-panel)', border: '1px solid var(--po-filetree-rail)',
            borderRadius: 10, padding: 24, maxWidth: 400, width: '90%',
          }}>
            <h3 style={{ margin: '0 0 12px', fontSize: 15, fontWeight: 500, color: 'var(--po-text)' }}>
              Confirm Rollback
            </h3>
            <p style={{ margin: '0 0 20px', fontSize: 13, color: 'var(--po-text-muted)', lineHeight: 1.5 }}>
              Restores the project scope to the state at{' '}
              <strong title={rollbackConfirm}>{shortCommit(rollbackConfirm)}</strong> by
              creating a new forward commit. Current head stays in history and can be
              re-applied later.
            </p>
            {rollbackError && (
              <p style={{ margin: '0 0 16px', fontSize: 12, color: 'var(--po-danger)', lineHeight: 1.5 }}>
                {rollbackError}
              </p>
            )}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <ActionButton
                onClick={() => { setRollbackConfirm(null); setRollbackError(null); }}
                disabled={isRollingBack}
              >
                Cancel
              </ActionButton>
              <ActionButton
                onClick={() => handleRollback(rollbackConfirm)}
                variant='warning'
                loading={isRollingBack}
              >
                {isRollingBack ? 'Rolling back...' : `Rollback to ${shortCommit(rollbackConfirm)}`}
              </ActionButton>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
