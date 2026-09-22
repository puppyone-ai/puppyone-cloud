/**
 * Visual tokens shared across the access-points feature.
 *
 * Re-exported from a single module so a future move to CSS variables /
 * Tailwind theme extension is one diff away. Component files reference
 * these by name; do NOT inline raw hex values back into JSX.
 */

export const COLOR_FG = 'var(--po-text)';
export const COLOR_FG_MUTED = 'var(--po-text-muted)';
export const COLOR_FG_DIM = 'var(--po-text-subtle)';

export const COLOR_BORDER = 'var(--po-border-subtle)';
export const COLOR_BORDER_HOVER = 'var(--po-border-strong)';

export const COLOR_BG_CARD = 'var(--po-panel)';
export const COLOR_BG_HOVER = 'var(--po-hover)';
export const COLOR_BG_SUNKEN = 'var(--po-inset)';
export const COLOR_BG_DASHED = 'var(--po-control)';

export const COLOR_DANGER = 'var(--po-danger)';
export const COLOR_DANGER_FAINT = 'var(--po-danger)';
export const COLOR_DANGER_BG = 'color-mix(in srgb, var(--po-danger) 12%, transparent)';
export const COLOR_DANGER_BORDER = 'color-mix(in srgb, var(--po-danger) 32%, transparent)';

export const COLOR_SUCCESS = 'var(--po-success)';
export const COLOR_SUCCESS_BORDER = 'color-mix(in srgb, var(--po-success) 55%, transparent)';
export const COLOR_ACCESS_ACTION = 'var(--po-access-action)';
export const COLOR_ACCESS_ACTION_BORDER = 'var(--po-access-action-border)';
export const COLOR_ACCESS_ACTIVE_BG = 'var(--po-access-active-bg)';
export const COLOR_ACCESS_ACTIVE_HOVER = 'var(--po-access-active-hover)';
export const COLOR_ACCESS_ACTIVE_TEXT = 'var(--po-access-active-text)';

export const COLOR_ACCENT = 'var(--po-accent)';
export const COLOR_ACCENT_TEXT_BRIGHT = 'var(--po-accent-text)';
export const COLOR_ACCENT_BG_FAINT = 'color-mix(in srgb, var(--po-accent) 6%, transparent)';
export const COLOR_ACCENT_BG = 'color-mix(in srgb, var(--po-accent) 12%, transparent)';
export const COLOR_ACCENT_BORDER = 'color-mix(in srgb, var(--po-accent) 28%, transparent)';
export const COLOR_ACCENT_BORDER_BRIGHT = 'color-mix(in srgb, var(--po-accent) 34%, transparent)';

export const FONT_MONO =
  "var(--po-font-mono)";

export const PANEL_BG = 'var(--po-canvas)';

export const ACCESS_PANEL_TYPOGRAPHY = {
  title: {
    fontSize: 13,
    lineHeight: '18px',
    fontWeight: 600,
    letterSpacing: 0,
  },
  label: {
    fontSize: 12,
    lineHeight: '16px',
    fontWeight: 500,
    letterSpacing: 0,
  },
  body: {
    fontSize: 12,
    lineHeight: '16px',
    fontWeight: 400,
    letterSpacing: 0,
  },
  meta: {
    fontSize: 11,
    lineHeight: '14px',
    fontWeight: 500,
    letterSpacing: 0,
  },
} as const;
