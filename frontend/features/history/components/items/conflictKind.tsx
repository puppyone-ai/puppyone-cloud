'use client';

import { Dots } from '@/components/loading';
import { StatusDot } from '@/components/ui/StatusDot';
import {
  encodeResolutionFiles,
  getPendingConflict,
  listPendingConflicts,
  resolveConflict,
  type ConflictRecord,
  type PendingConflictDetail,
} from '@/lib/conflictApi';
import {
  registerKind,
  type ConflictItem,
  type NeedsActionRenderContext,
} from '@/lib/needsActionRegistry';
import { snoozeUntil } from '@/lib/needsActionSnooze';
import React, { useEffect, useState } from 'react';

/**
 * Conflict kind: human-required three-way conflict
 * (``resolver_kind='human' OR policy='manual_review'``).
 *
 * v1 resolution surface:
 *   - Per file, a textarea seeded with Git-style conflict markers
 *     (``<<<<<<< Yours / ======= / >>>>>>> Theirs``). User edits the
 *     final bytes in place.
 *   - Footer: ``Apply resolutions`` (builds ``resolution_files`` from
 *     the textareas), ``Accept engine's tree`` (uses ``resolution_tree_id``
 *     = ``proposed_tree_id``, lands the engine's marker-merged tree
 *     so the user can resolve later in the regular editor), ``Snooze``.
 *
 * NOT in v1: "Keep yours" / "Keep theirs" one-click pills. They look
 * trivial but require the FULL file content of each side, and the
 * backend's ``ConflictRecord`` only carries the loser's content
 * truncated to 500 chars (see ``merge.py:243``) and the ``kept``
 * field is a label string, not content. Wiring those pills against
 * incomplete data would corrupt long files. We'll add them back once
 * the backend exposes a per-side full-content endpoint.
 *
 * Mid-resolution state lives in localStorage as a per-conflict draft
 * so the user can leave and return without losing edits — the backend
 * doesn't expose a draft primitive yet (PUP-5 §6 D4).
 */

const KIND_LABEL = 'Conflict';
const ACCENT_VAR = 'var(--po-warning)';
const DRAFT_KEY_PREFIX = 'puppyone:needs-action:draft:conflict:';

async function fetchItems(projectId: string): Promise<ConflictItem[]> {
  const rows = await listPendingConflicts(projectId);
  return rows
    .filter(
      (r) =>
        r.status === 'pending'
        && (r.resolver_kind === 'human' || r.policy === 'manual_review'),
    )
    .map<ConflictItem>((r) => ({
      kind: 'conflict',
      id: r.pending_conflict_id,
      scope_path: r.scope_path,
      created_at: r.created_at,
      source: r,
    }));
}

function renderRow(item: ConflictItem, ctx: NeedsActionRenderContext): React.ReactNode {
  return <ConflictRow item={item} ctx={ctx} />;
}

function renderDetail(item: ConflictItem, ctx: NeedsActionRenderContext): React.ReactNode {
  return <ConflictDetail item={item} ctx={ctx} />;
}

registerKind<ConflictItem>({
  kind: 'conflict',
  label: KIND_LABEL,
  description: 'Conflicts that need a human resolution',
  accentVar: ACCENT_VAR,
  fetchItems,
  refreshIntervalMs: 30_000,
  renderRow,
  renderDetail,
});

// ── Row ──────────────────────────────────────────────────────────────

function ConflictRow({ item, ctx }: { item: ConflictItem; ctx: NeedsActionRenderContext }) {
  const fileCount = item.source.changed_paths?.length ?? 0;
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
          {fileCount} file{fileCount === 1 ? '' : 's'} · 3-way merge
          {item.created_at && ` · ${formatRelative(item.created_at)}`}
        </div>
      </div>
      <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--po-text-subtle)] opacity-0 transition-opacity group-hover:opacity-100">
        Resolve
      </span>
    </button>
  );
}

// ── Detail ───────────────────────────────────────────────────────────

interface ConflictDraft {
  /** Manual-merge text per file path. Only populated for files the
   *  user actually opened the textarea for — files left untouched
   *  default to the seeded markers when ``Apply resolutions`` builds
   *  the payload. */
  manualText: Record<string, string>;
}

function loadDraft(id: string): ConflictDraft {
  if (typeof window === 'undefined') return { manualText: {} };
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY_PREFIX + id);
    if (!raw) return { manualText: {} };
    const parsed = JSON.parse(raw) as Partial<ConflictDraft>;
    return { manualText: parsed.manualText ?? {} };
  } catch {
    return { manualText: {} };
  }
}

function saveDraft(id: string, draft: ConflictDraft): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(DRAFT_KEY_PREFIX + id, JSON.stringify(draft));
  } catch {
    /* quota / private mode — swallow */
  }
}

function clearDraft(id: string): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(DRAFT_KEY_PREFIX + id);
  } catch {
    /* ignore */
  }
}

function ConflictDetail({ item, ctx }: { item: ConflictItem; ctx: NeedsActionRenderContext }) {
  const [detail, setDetail] = useState<PendingConflictDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'apply' | 'accept_tree' | 'reject' | null>(null);
  const [draft, setDraft] = useState<ConflictDraft>(() => loadDraft(item.id));

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
        setError(err instanceof Error ? err.message : 'Failed to load conflict');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ctx.projectId, item.id]);

  // Persist draft on every change. Cheap (one key per conflict, small
  // JSON); avoids the "I closed the panel and lost my work" rage.
  useEffect(() => {
    saveDraft(item.id, draft);
  }, [item.id, draft]);

  const conflictRecords: ConflictRecord[] = detail?.conflict_records ?? [];

  /** Resolve by submitting per-file edited bytes for every conflict
   *  record. Files the user didn't touch keep the default markers —
   *  intentionally honest: we're saying "you have to take a stance,
   *  even leaving markers means you'll see them in the file later". */
  const handleApply = async () => {
    if (!detail) return;
    const files: Record<string, string> = {};
    for (const r of conflictRecords) {
      files[r.path] = draft.manualText[r.path] ?? defaultManualText(r);
    }
    setBusy('apply');
    setError(null);
    try {
      const resp = await resolveConflict(ctx.projectId, item.id, {
        decision: 'accept',
        resolution_files: encodeResolutionFiles(files),
        resolution_message: `Manual resolution for ${formatScope(item.scope_path)}`,
      });
      clearDraft(item.id);
      ctx.onResolved({ reason: 'resolved', commit_id: resp.commit_id });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Apply failed');
      setBusy(null);
    }
  };

  /** Resolve by accepting the engine's proposed tree as-is. The
   *  tree usually contains files with embedded ``<<<<<<<`` markers
   *  that the user can resolve later in the regular editor. This is
   *  the "I'll deal with it later" path. */
  const handleAcceptTree = async () => {
    if (!detail) return;
    setBusy('accept_tree');
    setError(null);
    try {
      const resp = await resolveConflict(ctx.projectId, item.id, {
        decision: 'accept',
        resolution_tree_id: detail.proposed_tree_id,
        resolution_message: `Accept engine's marker tree for ${formatScope(item.scope_path)}`,
      });
      clearDraft(item.id);
      ctx.onResolved({ reason: 'resolved', commit_id: resp.commit_id });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Accept failed');
      setBusy(null);
    }
  };

  const handleReject = async () => {
    if (!detail) return;
    if (!confirm('Reject this conflict resolution? The pending row stays for someone else to resolve.')) return;
    setBusy('reject');
    setError(null);
    try {
      await resolveConflict(ctx.projectId, item.id, {
        decision: 'reject',
        resolution_message: `Reject manual resolution for ${formatScope(item.scope_path)}`,
      });
      clearDraft(item.id);
      ctx.onResolved({ reason: 'rejected' });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Reject failed');
      setBusy(null);
    }
  };

  const handleSnooze = () => {
    snoozeUntil({ projectId: ctx.projectId, kind: 'conflict', id: item.id });
    ctx.onSnoozed();
  };

  const setManualText = (path: string, text: string) => {
    setDraft((d) => ({ ...d, manualText: { ...d.manualText, [path]: text } }));
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
            background: `color-mix(in srgb, ${ACCENT_VAR} 12%, transparent)`,
            border: `1px solid color-mix(in srgb, ${ACCENT_VAR} 28%, transparent)`,
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
            <Dots size="xs" /> Loading conflict…
          </div>
        )}
        {error && (
          <div
            className="text-[13px] text-[var(--po-danger)]"
            role="alert"
            style={{ marginBottom: 12 }}
          >
            {error}
          </div>
        )}
        {detail && conflictRecords.length === 0 && (
          <div className="text-[13px] text-[var(--po-text-subtle)]">
            No per-file conflict records to inspect. Accept the engine&apos;s
            proposed tree below or reject the resolution.
          </div>
        )}
        {detail && conflictRecords.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            {conflictRecords.map((r) => (
              <FileConflictBlock
                key={r.path}
                record={r}
                manualText={draft.manualText[r.path] ?? defaultManualText(r)}
                onManualText={(t) => setManualText(r.path, t)}
              />
            ))}
          </div>
        )}
      </div>

      <footer
        style={{
          padding: '12px 20px',
          borderTop: '1px solid var(--po-divider)',
          display: 'flex',
          gap: 8,
          alignItems: 'center',
          flexShrink: 0,
          background: 'var(--po-canvas)',
        }}
      >
        <button
          type="button"
          onClick={handleApply}
          disabled={!detail || busy !== null}
          style={primaryButtonStyle(!detail || busy !== null)}
        >
          {busy === 'apply' ? <Dots size="xs" /> : null}
          {busy === 'apply' ? 'Applying…' : 'Apply resolutions'}
        </button>
        <button
          type="button"
          onClick={handleAcceptTree}
          disabled={!detail || busy !== null}
          style={secondaryButtonStyle(!detail || busy !== null)}
          title="Land the engine's marker-merged tree as-is. Files keep <<<<<<< markers so you can resolve them later in the regular editor."
        >
          {busy === 'accept_tree' ? <Dots size="xs" /> : null}
          {busy === 'accept_tree' ? 'Accepting…' : 'Accept engine tree'}
        </button>
        <button
          type="button"
          onClick={handleReject}
          disabled={!detail || busy !== null}
          style={secondaryButtonStyle(!detail || busy !== null)}
        >
          {busy === 'reject' ? <Dots size="xs" /> : null}
          {busy === 'reject' ? 'Rejecting…' : 'Reject'}
        </button>
        <button
          type="button"
          onClick={handleSnooze}
          disabled={busy !== null}
          style={ghostButtonStyle(busy !== null)}
        >
          Save draft &amp; snooze 24h
        </button>
      </footer>
    </div>
  );
}

/**
 * One file block in the conflict detail.
 *
 * v1 surface: a single inline textarea seeded with Git-style conflict
 * markers. The user edits to produce the final bytes. We deliberately
 * don't ship Keep-yours / Keep-theirs pills here — see the module
 * docstring for why.
 */
function FileConflictBlock({
  record,
  manualText,
  onManualText,
}: {
  record: ConflictRecord;
  manualText: string;
  onManualText: (t: string) => void;
}) {
  const lostTruncated = (record.lost_content ?? '').length >= 500;
  return (
    <div
      style={{
        border: '1px solid var(--po-border-subtle)',
        borderRadius: 8,
        overflow: 'hidden',
        background: 'var(--po-panel)',
      }}
    >
      <div
        style={{
          padding: '10px 14px',
          borderBottom: '1px solid var(--po-border-subtle)',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          fontSize: 12,
          fontFamily: 'var(--po-font-mono, ui-monospace, monospace)',
          color: 'var(--po-text)',
        }}
      >
        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {record.path}
        </span>
        {record.strategy && (
          <span
            style={{
              fontSize: 10,
              padding: '2px 6px',
              borderRadius: 4,
              background: 'var(--po-control)',
              color: 'var(--po-text-subtle)',
              fontFamily: 'var(--po-font-sans)',
            }}
            title={record.detail ?? undefined}
          >
            {record.strategy}
          </span>
        )}
      </div>
      <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {lostTruncated && (
          <div
            style={{
              fontSize: 11,
              color: 'var(--po-text-subtle)',
              fontStyle: 'italic',
            }}
          >
            Note: the losing side&apos;s preview is truncated to 500 chars by
            the engine. Edit the manual merge directly to land the final
            bytes you want.
          </div>
        )}
        <textarea
          value={manualText}
          onChange={(e) => onManualText(e.target.value)}
          style={{
            width: '100%',
            minHeight: 160,
            maxHeight: 480,
            padding: 10,
            borderRadius: 6,
            border: '1px solid var(--po-border-strong)',
            background: 'var(--po-inset)',
            color: 'var(--po-text)',
            fontFamily: 'var(--po-font-mono, ui-monospace, monospace)',
            fontSize: 12,
            lineHeight: 1.5,
            resize: 'vertical',
          }}
          spellCheck={false}
        />
      </div>
    </div>
  );
}

function defaultManualText(record: ConflictRecord): string {
  // Seed the manual editor with whatever real content we have for the
  // losing side, plus instructional comments around it. Crucially we
  // do NOT embed ``record.kept`` — that field is a LABEL string from
  // the engine ("merged"/"theirs"/"ours"), not the kept content.
  // The winning side's real bytes live in ``proposed_tree_id`` and
  // aren't transmitted in ConflictRecord; use the "Accept engine
  // tree" button if you want them as-is.
  const lost = (record.lost_content ?? '').trim();
  const lostBlock = lost ? lost : '(empty)';
  const truncatedNote =
    (record.lost_content ?? '').length >= 500
      ? ' [TRUNCATED: engine kept only the first 500 chars]'
      : '';
  return [
    `# Conflict: ${record.path}`,
    `# Engine strategy: ${record.strategy}${record.detail ? ` — ${record.detail}` : ''}`,
    `# Other side's content${truncatedNote}:`,
    lostBlock,
    '',
    "# Replace everything above with the final content you want to land.",
    "# Or click 'Accept engine tree' to land the marker-merged version instead.",
    '',
  ].join('\n');
}

// ── Helpers (duplicated from pendingReviewKind on purpose; the two
//    files are otherwise independent and we want each kind to be
//    one-file readable for plugin authors) ─────────────────────────

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
