/**
 * Design tokens shared across the access page modules.
 *
 * Lifted out of the original 2478-line `page.tsx` so colour / font
 * changes have a single source of truth. Values intentionally point at
 * global CSS variables so this page follows Light / Dark / System mode
 * without maintaining a parallel palette.
 */

export const T = {
  bg: 'var(--po-canvas)',
  border: 'var(--po-border)',
  cardBg: 'var(--po-panel)',
  cardBorder: 'var(--po-border-subtle)',

  text1: 'var(--po-text)',
  text2: 'var(--po-text-muted)',
  text3: 'var(--po-text-disabled)',
  text4: 'var(--po-filetree-rail)',

  fontSans: 'var(--po-font-sans)',
  fontMono: 'var(--po-font-mono)',
  ease: 'cubic-bezier(0.16, 1, 0.3, 1)',
} as const;

// Two button sizes only. Section-level ghost actions (Edit Scope, View
// all, Copy connect) all share `GhostButton`; primary actions on the
// Identity row (Pause/Resume, More) share `PrimaryGhostButton`. Both
// pull from the same neutral palette (transparent → var(--po-hover) on
// hover), so we no longer have three different sizes / fonts
// / colors competing for attention on the same screen.
export const BTN_RADIUS = 6;

// PromptBlock — a copyable block that previews the AI-agent prompt
// the user can paste into ChatGPT / Claude. Height is fixed so the
// surface doesn't jump when the prompt changes; the bottom 64px
// fades to PROMPT_BG so long prompts don't appear visually clipped.
export const PROMPT_BLOCK_HEIGHT = 140;
export const PROMPT_BG = 'var(--po-inset)';

// Geometry mirrors `ExplorerTreeRow` exactly — every dimension here is
// the same one the data-view sidebar uses, so the mount-point preview
// and the live tree feel like the same control. Folder/file glyphs at
// 16px, rows at 30px, depth-1 indent at 16px, elbow stem at x=16,
// hook at y=15, hook width 8. Don't drift these without updating
// `ExplorerTreeRow` too.
export const TREE_ROW_HEIGHT = 30;
export const TREE_INDENT = 16;
export const TREE_ICON_SIZE = 16;
export const TREE_LINE_COLOR = 'var(--po-tree-guide)';

// Cap on rows shown in the scope's file-tree preview before we
// collapse to "+N more" — see `nodesToPreview` in lib/format.ts.
export const SCOPE_ROWS_LIMIT = 4;
