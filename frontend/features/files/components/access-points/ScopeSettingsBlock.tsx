'use client';

import { Dots } from '@/components/loading';
import {
  COLOR_BG_CARD,
  COLOR_BG_SUNKEN,
  COLOR_BORDER,
  COLOR_BORDER_HOVER,
  COLOR_DANGER,
  COLOR_DANGER_BG,
  COLOR_DANGER_BORDER,
  COLOR_DANGER_FAINT,
  COLOR_FG,
  COLOR_FG_DIM,
  COLOR_FG_MUTED,
} from '@/features/files/components/access-points/tokens';
import {
  deleteScope,
  updateScope,
  type RepositoryView,
} from '@/lib/repoApi';
import { useCallback, useEffect, useState } from 'react';

/**
 * ScopeSettingsBlock — the full Settings panel for an access point.
 *
 * Keep this page lightweight. Connector-level capabilities now own the
 * detailed permission story, so Settings only keeps Scope identity and
 * deletion. Credential lifecycle belongs to each Access Surface.
 *
 * The block renders as the dedicated Settings sub-page for a scope.
 * When dirty, a Save / Discard footer appears at the bottom so the
 * user can commit a batch of edits (rather than per-control auto-PATCH,
 * which would make the destructive `rw → r` flip happen mid-form).
 *
 * Dirty state is reported up via `onDirtyChange` so the parent can:
 *   - show an "unsaved" indicator on the Settings header toggle
 *   - confirm before collapsing the block or closing the panel
 *
 * Save → updateScope → onMutated (refresh SWR caches) → resets dirty,
 * stays mounted (user can keep editing). Delete → onScopeDeleted
 * (panel close). The Project root view has no Scope mutation controls.
 */
export function ScopeSettingsBlock({
  scope,
  projectId,
  onMutated,
  onScopeDeleted,
  onDirtyChange,
  accessMethods,
}: {
  readonly scope: RepositoryView;
  readonly projectId: string;
  readonly onMutated: () => Promise<unknown>;
  readonly onScopeDeleted: () => void;
  readonly accessMethods?: React.ReactNode;
  /** Lift dirty state to parent so the panel chrome ([⚙ Settings]
   *  toggle / [×] close) can confirm before discarding edits. Called
   *  on every dirty-change transition; the parent stores it as React
   *  state and gates close handlers on it. */
  readonly onDirtyChange?: (dirty: boolean) => void;
}) {
  const [name, setName] = useState(scope.name);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Per-action confirm states.
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const isProjectRoot = scope.target.kind === 'project_root';

  // Reset everything when the user navigates to a different scope so a
  // half-armed destructive action doesn't leak across.
  useEffect(() => {
    setName(scope.name);
    setError(null);
    setConfirmDelete(false);
  }, [scope.id, scope.name]);

  const dirty = !isProjectRoot && name.trim() !== scope.name;

  // Push dirty up. Effect runs after render so consumers see consistent
  // state. The dependency on `onDirtyChange` itself is fine because
  // parent should pass a stable callback (usually via useCallback);
  // if not, the worst case is one extra notification per render.
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  // ── Mutations ─────────────────────────────────────────────────────────

  const handleSave = useCallback(async () => {
    if (scope.target.kind !== 'scope') return;
    setSaving(true);
    setError(null);
    try {
      await updateScope(projectId, scope.id, {
        name: name.trim() || scope.name,
      });
      await onMutated();
      // Stays mounted; SWR refresh will re-pass scope props and the
      // reset effect will sync local form back to the saved values
      // (dirty becomes false → footer auto-hides).
    } catch (e) {
      setError((e as Error).message || 'Failed to save');
    } finally {
      setSaving(false);
    }
  }, [projectId, scope, name, onMutated]);

  const handleDiscard = useCallback(() => {
    setName(scope.name);
    setError(null);
  }, [scope.name]);

  const handleDelete = useCallback(async () => {
    if (scope.target.kind !== 'scope') return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      // Auto-disarm in 4s so an accidental first click doesn't leave
      // the button in a destructive state if the user wanders off.
      setTimeout(() => setConfirmDelete(false), 4000);
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deleteScope(projectId, scope.id);
      await onMutated();
      onScopeDeleted();
    } catch (e) {
      setError((e as Error).message || 'Failed to delete');
      setDeleting(false);
      setConfirmDelete(false);
    }
  }, [confirmDelete, projectId, scope, onMutated, onScopeDeleted]);

  // ── Render helpers ────────────────────────────────────────────────────

  return (
    <section
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      {!isProjectRoot ? <Card>
        <FieldLabel>Name</FieldLabel>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{
            background: COLOR_BG_SUNKEN,
            border: `1px solid ${COLOR_BORDER}`,
            borderRadius: 6,
            color: COLOR_FG,
            fontSize: 12,
            padding: '6px 10px',
            outline: 'none',
          }}
        />
      </Card> : (
        <Card>
          <FieldLabel>Project repository</FieldLabel>
          <FieldHelp>
            This is the Project-owned canonical root. Configure credentials on
            its CLI and Git access methods; path Scope controls do not apply.
          </FieldHelp>
        </Card>
      )}

      {accessMethods}

      {!isProjectRoot ? <Card danger>
        <FieldLabel danger>Danger zone</FieldLabel>
        <FieldHelp>
          Deletes this access point and cascades to its built-in cli + agent
          connectors. Bound third-party integrations must be removed first.
        </FieldHelp>
        <button
          type="button"
          onClick={handleDelete}
          disabled={deleting}
          style={{
            alignSelf: 'flex-start',
            height: 30,
            padding: '0 12px',
            fontSize: 12,
            fontWeight: 500,
            color: confirmDelete
                ? COLOR_DANGER_FAINT
                : COLOR_DANGER,
            background: confirmDelete
                ? COLOR_DANGER_BG
                : 'transparent',
            border: `1px solid ${
              confirmDelete
                  ? COLOR_DANGER
                  : COLOR_DANGER_BORDER
            }`,
            borderRadius: 6,
            cursor: 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          {deleting && <Dots size="xs" tone="danger" />}
          {deleting
            ? 'Deleting…'
            : confirmDelete
              ? 'Confirm delete'
              : 'Delete access point'}
        </button>
      </Card> : null}

      {error && (
        <div
          style={{
            fontSize: 12,
            color: COLOR_DANGER_FAINT,
            padding: '8px 12px',
            borderRadius: 6,
            background: COLOR_DANGER_BG,
            border: `1px solid ${COLOR_DANGER_BORDER}`,
          }}
        >
          {error}
        </div>
      )}

      {/* Save / Discard footer — only present when the form has dirty
          edits. Keeping it inline avoids a sticky strip competing with
          the settings rows. The parent panel confirms before leaving
          while dirty. */}
      {dirty && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 12px',
            borderRadius: 8,
            background: COLOR_BG_CARD,
            border: `1px solid ${COLOR_BORDER_HOVER}`,
          }}
        >
          <span style={{ fontSize: 12, color: COLOR_FG_MUTED, flex: 1 }}>
            Unsaved changes
          </span>
          <button
            type="button"
            onClick={handleDiscard}
            disabled={saving}
            style={{
              height: 30,
              padding: '0 12px',
              fontSize: 12,
              color: COLOR_FG,
              background: 'var(--po-hover)',
              border: `1px solid ${COLOR_BORDER_HOVER}`,
              borderRadius: 6,
              cursor: saving ? 'default' : 'pointer',
            }}
          >
            Discard
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            style={{
              height: 30,
              padding: '0 14px',
              fontSize: 12,
              fontWeight: 600,
              color: 'var(--po-inset)',
              background: saving ? COLOR_BORDER_HOVER : COLOR_FG,
              border: `1px solid ${saving ? COLOR_BORDER_HOVER : COLOR_FG}`,
              borderRadius: 6,
              cursor: saving ? 'default' : 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            {saving && <Dots size="xs" />}
            {saving ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      )}
    </section>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────

function Card({
  children,
  danger = false,
}: {
  readonly children: React.ReactNode;
  readonly danger?: boolean;
}) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 9,
        padding: danger ? 12 : '0 0 16px',
        borderRadius: danger ? 8 : 0,
        border: danger ? `1px solid ${COLOR_DANGER_BORDER}` : 'none',
        borderBottom: danger ? 'none' : '1px solid var(--po-divider)',
        background: danger
          ? 'color-mix(in srgb, var(--po-danger) 6%, transparent)'
          : 'transparent',
      }}
    >
      {children}
    </div>
  );
}

function FieldLabel({
  children,
  danger = false,
}: {
  readonly children: React.ReactNode;
  readonly danger?: boolean;
}) {
  return (
    <div
      style={{
        fontSize: 14,
        fontWeight: 600,
        color: danger ? COLOR_DANGER_FAINT : COLOR_FG,
      }}
    >
      {children}
    </div>
  );
}

function FieldHelp({ children }: { readonly children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 12,
        lineHeight: 1.45,
        color: COLOR_FG_DIM,
      }}
    >
      {children}
    </div>
  );
}
