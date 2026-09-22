/**
 * Local design tokens for the Workflow surface. Mirrors the ``T``
 * object in ``settings/page.tsx`` and ``access/lib/tokens.ts`` so this
 * surface reads as the same family.
 */
export const T = {
  bg: 'var(--po-canvas)',
  border: 'var(--po-border)',
  cardBg: 'var(--po-panel)',
  cardBorder: 'var(--po-border-subtle)',
  cardBorderStrong: 'var(--po-border-strong)',
  text1: 'var(--po-text)',
  text2: 'var(--po-text-muted)',
  text3: 'var(--po-text-disabled)',
  text4: 'var(--po-text-subtle)',
  accent: 'var(--po-accent)',
  success: 'var(--po-success)',
  danger: 'var(--po-danger)',
  warning: 'var(--po-warning)',
  fontSans:
    'var(--po-font-sans)',
  fontMono:
    'var(--po-font-mono)',
  ease: 'cubic-bezier(0.16, 1, 0.3, 1)',
} as const;
