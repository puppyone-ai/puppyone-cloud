'use client';

import { Dots } from '@/components/loading';
import { StatusDot } from '@/components/ui/StatusDot';
import {
  getPendingConflict,
  listPendingConflicts,
  resolveConflict,
  type PendingConflictDetail,
} from '@/lib/conflictApi';
import {
  registerKind,
  type NeedsActionRenderContext,
  type PendingReviewItem,
} from '@/lib/needsActionRegistry';
import { snoozeUntil } from '@/lib/needsActionSnooze';
import React, { useEffect, useState } from 'react';

/**
 * Pending review kind: a conflict where an agent already produced a
 * merge proposal (``resolver_kind='agent' AND policy='agent_review'``)
 * and is waiting for human OK to land.
 *
 * The row shows the agent's name + summary; the detail pane shows the
 * proposed file list with one-click "Accept agent merge" or escalation
 * to manual resolution.
 */

const KIND_LABEL = 'Pending review';
const ACCENT_VAR = 'var(--po-accent)';

async function fetchItems(projectId: string): Promise<PendingReviewItem[]> {
  const rows = await listPendingConflicts(projectId);
  return rows
    .filter(
      (r) =>
        r.status === 'pending'
        && r.resolver_kind === 'agent'
        && (r.policy === 'agent_review' || r.policy === 'agent_auto_resolve'),
    )
    .map<PendingReviewItem>((r) => ({
      kind: 'pending-review',
      id: r.pending_conflict_id,
      scope_path: r.scope_path,
      created_at: r.created_at,
      source: r,
    }));
}

function renderRow(item: PendingReviewItem, ctx: NeedsActionRenderContext): React.ReactNode {
  return <PendingReviewRow item={item} ctx={ctx} />;
}

function renderDetail(item: PendingReviewItem, ctx: NeedsActionRenderContext): React.ReactNode {
  return <PendingReviewDetail item={item} ctx={ctx} />;
}

registerKind<PendingReviewItem>({
  kind: 'pending-review',
  label: KIND_LABEL,
  description: 'Agent merge proposals waiting for your OK',
  accentVar: ACCENT_VAR,
  fetchItems,
  refreshIntervalMs: 30_000,
  renderRow,
  renderDetail,
});

// ── Row ──────────────────────────────────────────────────────────────

function PendingReviewRow({
  item,
  ctx,
}: {
  item: PendingReviewItem;
  ctx: NeedsActionRenderContext;
}) {
  const fileCount = item.source.changed_paths?.length ?? 0;
  const resolver = item.source.resolver_actor || 'Agent';
  return (
    <button
      type="button"
      onClick={ctx.onSelect}
      className={`group flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors ${
        ctx.isSelected ? 'bg-[var(--po-selected)]' : 'hover:bg-[var(--po-hover)]'
      }`}
    >
      <StatusDot style={{ background: ACCENT_VAR }} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12px] font-medium text-[var(--po-text)]">
          {formatScope(item.scope_path)}
        </div>
        <div className="truncate text-[11px] text-[var(--po-text-subtle)]">
          {fileCount} file{fileCount === 1 ? '' : 's'} · {resolver}
          {item.created_at && ` · ${formatRelative(item.created_at)}`}
        </div>
      </div>
      <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--po-text-subtle)] opacity-0 transition-opacity group-hover:opacity-100">
        Review
      </span>
    </button>
  );
}

// ── Detail ───────────────────────────────────────────────────────────

function PendingReviewDetail({
  item,
  ctx,
}: {
  item: PendingReviewItem;
  ctx: NeedsActionRenderContext;
}) {
  const [detail, setDetail] = useState<PendingConflictDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'accept' | 'reject' | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getPendingConflict(ctx.projectId, item.id)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Failed to load review');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ctx.projectId, item.id]);

  const handleAccept = async () => {
    if (!detail) return;
    setBusy('accept');
    try {
      const resp = await resolveConflict(ctx.projectId, item.id, {
        decision: 'accept',
        resolution_tree_id: detail.proposed_tree_id,
        resolution_message: `Accept agent merge for ${formatScope(item.scope_path)}`,
      });
      ctx.onResolved({ reason: 'resolved', commit_id: resp.commit_id });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Accept failed');
      setBusy(null);
    }
  };

  const handleReject = async () => {
    if (!detail) return;
    if (!confirm('Reject the agent merge and queue this for manual resolution?')) return;
    setBusy('reject');
    try {
      await resolveConflict(ctx.projectId, item.id, {
        decision: 'reject',
        resolution_message: `Reject agent merge for ${formatScope(item.scope_path)}`,
      });
      ctx.onResolved({ reason: 'rejected' });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Reject failed');
      setBusy(null);
    }
  };

  const handleSnooze = () => {
    snoozeUntil({ projectId: ctx.projectId, kind: 'pending-review', id: item.id });
    ctx.onSnoozed();
  };

  return (
    <div className="flex h-full flex-col">
      <header
        style={{
          padding: '14px 20px',
          borderBottom: '1px solid var(--po-divider)',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          flexShrink: 0,
        }}
      >
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '2px 8px',
            borderRadius: 4,
            fontSize: 11,
            fontWeight: 600,
            color: ACCENT_VAR,
            background: `color-mix(in srgb, ${ACCENT_VAR} 10%, transparent)`,
            border: `1px solid color-mix(in srgb, ${ACCENT_VAR} 25%, transparent)`,
          }}
        >
          <StatusDot style={{ background: ACCENT_VAR }} />
          {KIND_LABEL}
        </span>
        <span style={{ fontSize: 13, color: 'var(--po-text)' }}>
          {formatScope(item.scope_path)}
        </span>
        <span style={{ fontSize: 12, color: 'var(--po-text-subtle)' }}>
          #{item.id.slice(0, 8)}
        </span>
        {item.created_at && (
          <span style={{ fontSize: 12, color: 'var(--po-text-subtle)' }}>
            · {formatRelative(item.created_at)}
          </span>
        )}
      </header>

      <div className="flex-1 overflow-y-auto custom-scrollbar" style={{ padding: 20 }}>
        {loading && (
          <div className="flex items-center gap-2 text-[13px] text-[var(--po-text-muted)]">
            <Dots size="xs" /> Loading review…
          </div>
        )}
        {error && (
          <div className="text-[13px] text-[var(--po-danger)]" role="alert">
            {error}
          </div>
        )}
        {detail && (
          <>
            <section style={{ marginBottom: 20 }}>
              <h3 style={sectionTitleStyle}>Agent&apos;s proposal</h3>
              <ProposalSummary detail={detail} />
            </section>
            <section>
              <h3 style={sectionTitleStyle}>
                Files in this review{' '}
                <span style={{ color: 'var(--po-text-subtle)', fontWeight: 400 }}>
                  ({detail.changed_paths.length})
                </span>
              </h3>
              <FileList paths={detail.changed_paths} />
            </section>
          </>
        )}
      </div>

      <footer
        style={{
          padding: '12px 20px',
          borderTop: '1px solid var(--po-divider)',
          display: 'flex',
          gap: 8,
          flexShrink: 0,
          background: 'var(--po-canvas)',
        }}
      >
        <button
          type="button"
          onClick={handleAccept}
          disabled={!detail || busy !== null}
          style={primaryButtonStyle(busy === 'accept' || !detail)}
        >
          {busy === 'accept' ? <Dots size="xs" /> : null}
          {busy === 'accept' ? 'Accepting…' : 'Accept agent merge'}
        </button>
        <button
          type="button"
          onClick={handleReject}
          disabled={!detail || busy !== null}
          style={secondaryButtonStyle(busy === 'reject' || !detail)}
        >
          {busy === 'reject' ? <Dots size="xs" /> : null}
          Reject &amp; resolve manually
        </button>
        <button
          type="button"
          onClick={handleSnooze}
          disabled={busy !== null}
          style={ghostButtonStyle(busy !== null)}
        >
          Snooze 24h
        </button>
      </footer>
    </div>
  );
}

function ProposalSummary({ detail }: { detail: PendingConflictDetail }) {
  // The backend doesn't (yet) ship a structured "what the agent did"
  // summary. We approximate one from the conflict_records:
  //   - record.strategy is a terse code from the engine
  //     (``lww`` / ``modify_delete`` / ``line_merge`` etc.) that we
  //     translate to user-friendly text in ``strategyLabel`` below.
  // When backend learns to emit a narrative summary we'll prefer it
  // over this derived one.
  const records = detail.conflict_records;
  if (records.length === 0) {
    return (
      <div className="text-[12px] text-[var(--po-text-subtle)]">
        Agent merged {detail.changed_paths.length} file
        {detail.changed_paths.length === 1 ? '' : 's'} without conflicts.
      </div>
    );
  }
  return (
    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: 'var(--po-text-muted)', lineHeight: 1.6 }}>
      {records.slice(0, 6).map((r, i) => (
        <li key={`${r.path}-${i}`}>
          <span style={{ color: 'var(--po-text)' }}>{r.path}</span>{' '}
          <span style={{ color: 'var(--po-text-subtle)' }}>· {strategyLabel(r.strategy)}</span>
          {r.detail && <span style={{ color: 'var(--po-text-subtle)' }}> · {r.detail}</span>}
        </li>
      ))}
      {records.length > 6 && (
        <li style={{ color: 'var(--po-text-subtle)' }}>
          …and {records.length - 6} more
        </li>
      )}
    </ul>
  );
}

function FileList({ paths }: { paths: string[] }) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? paths : paths.slice(0, 12);
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
      {visible.map((p) => (
        <li
          key={p}
          style={{
            fontSize: 12,
            color: 'var(--po-text-muted)',
            padding: '4px 8px',
            borderRadius: 4,
            fontFamily: 'var(--po-font-mono, ui-monospace, monospace)',
          }}
        >
          {p}
        </li>
      ))}
      {!showAll && paths.length > 12 && (
        <li style={{ padding: '4px 8px' }}>
          <button
            type="button"
            onClick={() => setShowAll(true)}
            style={{
              fontSize: 12,
              color: 'var(--po-text-subtle)',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              padding: 0,
            }}
          >
            Show {paths.length - 12} more…
          </button>
        </li>
      )}
    </ul>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────

function formatScope(scope: string): string {
  if (!scope) return 'Project root';
  return scope;
}

function formatRelative(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const diff = Date.now() - t;
  if (diff < 60_000) return 'just now';
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

/** Map the engine's terse strategy codes to user-friendly text.
 *  Values come from ``backend/src/version_engine/write_engine/merge.py``
 *  and ``conflict_policy.py``. If the backend introduces a new strategy
 *  we fall back to a Title-Case rendering of the raw code so the user
 *  sees something readable rather than ``modify_delete``. */
function strategyLabel(s: string): string {
  switch (s) {
    case 'identical':
      return 'no change';
    case 'theirs_only':
      return 'only the other side changed';
    case 'ours_only':
      return 'only your side changed';
    case 'line_merge':
      return 'merged line-by-line';
    case 'import_merge':
      return 'merged on import';
    case 'append_only_merge':
      return 'append-only merge';
    case 'lww':
      return 'last-write-wins';
    case 'delete_modify':
      return 'you deleted, other side modified';
    case 'modify_delete':
      return 'you modified, other side deleted';
    case 'superseded_by_parent':
      return 'superseded by parent edit';
    case 'rejected':
      return 'rejected';
    default:
      return s.replace(/_/g, ' ');
  }
}

// ── Inline button styles (match the team / invite pages) ─────────────

const sectionTitleStyle: React.CSSProperties = {
  fontSize: 12,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  color: 'var(--po-text-subtle)',
  margin: '0 0 8px 0',
};

function primaryButtonStyle(disabled: boolean): React.CSSProperties {
  return {
    height: 32,
    padding: '0 14px',
    borderRadius: 6,
    border: 'none',
    background: 'var(--po-text)',
    color: 'var(--po-text-inverse)',
    fontSize: 13,
    fontWeight: 600,
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.5 : 1,
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
  };
}

function secondaryButtonStyle(disabled: boolean): React.CSSProperties {
  return {
    height: 32,
    padding: '0 14px',
    borderRadius: 6,
    border: '1px solid var(--po-border)',
    background: 'var(--po-control)',
    color: 'var(--po-text)',
    fontSize: 13,
    fontWeight: 500,
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.5 : 1,
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
  };
}

function ghostButtonStyle(disabled: boolean): React.CSSProperties {
  return {
    height: 32,
    padding: '0 12px',
    borderRadius: 6,
    border: 'none',
    background: 'transparent',
    color: 'var(--po-text-subtle)',
    fontSize: 12,
    fontWeight: 500,
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.5 : 1,
    marginLeft: 'auto',
  };
}
