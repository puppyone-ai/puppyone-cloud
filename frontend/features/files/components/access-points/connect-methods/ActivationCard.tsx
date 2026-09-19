'use client';

import {
  COLOR_BG_CARD,
  COLOR_BORDER,
  COLOR_BORDER_HOVER,
  COLOR_DANGER_FAINT,
  COLOR_FG,
  COLOR_FG_DIM,
  COLOR_FG_MUTED,
} from '@/features/files/components/access-points/tokens';

/**
 * ActivationCard — dashed card with one primary CTA + optional error.
 * Used by the AI Agent body for "activate" and "open chat" prompts.
 */
export function ActivationCard({
  title,
  body,
  actionLabel,
  disabled = false,
  error,
  onAction,
}: {
  readonly title: string;
  readonly body: string;
  readonly actionLabel: string;
  readonly disabled?: boolean;
  readonly error?: string | null;
  readonly onAction: () => void;
}) {
  return (
    <div
      style={{
        borderRadius: 8,
        border: `1px dashed ${COLOR_BORDER_HOVER}`,
        background: COLOR_BG_CARD,
        padding: '12px',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      <div style={{ fontSize: 12, color: COLOR_FG, fontWeight: 600 }}>{title}</div>
      <div style={{ fontSize: 12, color: COLOR_FG_MUTED, lineHeight: 1.55 }}>{body}</div>
      <button
        type="button"
        onClick={onAction}
        disabled={disabled}
        style={{
          alignSelf: 'flex-start',
          height: 30,
          padding: '0 12px',
          fontSize: 12,
          fontWeight: 600,
          color: disabled ? COLOR_FG_DIM : 'var(--po-text-inverse)',
          background: disabled ? 'var(--po-border-subtle)' : 'var(--po-text)',
          border: `1px solid ${disabled ? COLOR_BORDER : 'var(--po-text)'}`,
          borderRadius: 6,
          cursor: disabled ? 'not-allowed' : 'pointer',
        }}
      >
        {actionLabel}
      </button>
      {error && (
        <div style={{ fontSize: 10, color: COLOR_DANGER_FAINT, lineHeight: 1.5 }}>
          {error}
        </div>
      )}
    </div>
  );
}
