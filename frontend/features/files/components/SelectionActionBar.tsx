'use client';

/**
 * SelectionActionBar — floats above the grid view while one or more
 * items are multi-selected.
 *
 * Anchored bottom-center so it doesn't fight with the per-item action
 * menu (top-right) or the breadcrumb/editor chrome (top).
 *
 * Visible only when ``count > 0``. The Esc / Delete hotkeys are wired
 * by the parent — this component is purely presentational.
 */

import { useEffect, useState } from 'react';

interface SelectionActionBarProps {
  count: number;
  onClear: () => void;
  onDelete: () => void;
  /** Optional: when ``true``, the Delete button shows a spinner and
   *  is disabled. Lets the parent block double-submits while the
   *  bulk-delete request is in flight. */
  busy?: boolean;
  /** OS detection for the keyboard-shortcut hint. */
  shortcutHint?: string;
}

export function SelectionActionBar({
  count,
  onClear,
  onDelete,
  busy = false,
  shortcutHint,
}: SelectionActionBarProps) {
  // Animate in/out with a short delay so flicking selections on/off
  // doesn't strobe the bar. We mount immediately when count > 0 and
  // unmount after a short fade-out when it drops to 0.
  const [visible, setVisible] = useState(count > 0);
  useEffect(() => {
    if (count > 0) {
      setVisible(true);
      return;
    }
    const t = setTimeout(() => setVisible(false), 140);
    return () => clearTimeout(t);
  }, [count]);

  if (!visible) return null;

  return (
    <div
      role="toolbar"
      aria-label="Selection actions"
      style={{
        position: 'fixed',
        left: '50%',
        bottom: 56,
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '8px 14px 8px 16px',
        background: 'var(--po-overlay)',
        border: '1px solid var(--po-border)',
        borderRadius: 12,
        boxShadow: '0 12px 32px var(--po-shadow), 0 0 0 1px var(--po-border) inset',
        color: 'var(--po-text)',
        fontSize: 13,
        fontWeight: 500,
        zIndex: 60,
        opacity: count > 0 ? 1 : 0,
        transition: 'opacity 140ms ease, transform 140ms ease',
        transform: count > 0
          ? 'translate(-50%, 0)'
          : 'translate(-50%, 6px)',
        pointerEvents: count > 0 ? 'auto' : 'none',
      }}
    >
      <span style={{ color: 'var(--po-text-muted)' }}>
        <span style={{ color: 'var(--po-text)', fontWeight: 600 }}>{count}</span>
        {' '}
        selected
      </span>

      <span aria-hidden style={{ width: 1, height: 18, background: 'var(--po-border)' }} />

      <button
        type="button"
        onClick={onClear}
        title={shortcutHint ? `Clear selection (Esc)` : 'Clear selection'}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          height: 30,
          padding: '0 10px',
          background: 'transparent',
          border: '1px solid transparent',
          borderRadius: 7,
          color: 'var(--po-text-muted)',
          fontSize: 12.5,
          fontWeight: 500,
          cursor: 'pointer',
          transition: 'background 120ms ease, color 120ms ease',
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLButtonElement).style.background = 'var(--po-hover)';
          (e.currentTarget as HTMLButtonElement).style.color = 'var(--po-text)';
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
          (e.currentTarget as HTMLButtonElement).style.color = 'var(--po-text-muted)';
        }}
      >
        Clear
        <span style={{ opacity: 0.6, fontSize: 10.5, fontVariantNumeric: 'tabular-nums' }}>Esc</span>
      </button>

      <button
        type="button"
        onClick={onDelete}
        disabled={busy}
        title="Delete selected items (Delete)"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          height: 30,
          padding: '0 12px',
          background: busy ? 'color-mix(in srgb, var(--po-danger) 40%, transparent)' : 'var(--po-danger)',
          border: '1px solid color-mix(in srgb, var(--po-danger) 55%, transparent)',
          borderRadius: 7,
          color: 'var(--po-text-inverse)',
          fontSize: 12.5,
          fontWeight: 600,
          cursor: busy ? 'progress' : 'pointer',
          transition: 'background 120ms ease',
        }}
        onMouseEnter={(e) => {
          if (busy) return;
          (e.currentTarget as HTMLButtonElement).style.background = 'var(--po-danger)';
          (e.currentTarget as HTMLButtonElement).style.opacity = '0.9';
        }}
        onMouseLeave={(e) => {
          if (busy) return;
          (e.currentTarget as HTMLButtonElement).style.background = 'var(--po-danger)';
          (e.currentTarget as HTMLButtonElement).style.opacity = '1';
        }}
      >
        {busy ? 'Deleting…' : 'Delete'}
        {!busy && (
          <span style={{ opacity: 0.7, fontSize: 10.5, fontVariantNumeric: 'tabular-nums' }}>⌫</span>
        )}
      </button>
    </div>
  );
}
