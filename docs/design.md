# PuppyOne Design Guidelines

This document captures product-level visual decisions that should remain stable
across PuppyOne surfaces. Use it as the first reference before introducing local
component colors, button treatments, typography, or spacing.

For broader frontend theme architecture, see
[`docs/frontend/color-mode-architecture.md`](frontend/color-mode-architecture.md).
For shared product UI rhythm, see
[`docs/frontend/product-visual-system.md`](frontend/product-visual-system.md).

## Theme Blues

PuppyOne uses different blue treatments in light and dark themes. Do not invent
local blue-gray variants when a surface needs a primary action.

### Light Theme

- Product primary blue: `#4599DF`.
- Primary action hover: `#3489D0`.
- Primary action foreground: `var(--po-text-inverse)`.
- Primary action border: none.
- Disabled primary action background:
  `color-mix(in srgb, #4599DF 14%, transparent)`.
- Disabled primary action foreground:
  `color-mix(in srgb, #4599DF 44%, var(--po-text-disabled))`.

Use this treatment for primary command buttons that must remain visible in the
light desktop shell, such as Git `Pull`, `Commit`, `Commit & Push`, `Publish`,
and `Push`.

### Dark Theme

- Use the current dark desktop accent token for primary actions:
  `var(--po-accent)`.
- Current desktop fallback value: `#2563EB`.
- Hover:
  `color-mix(in srgb, var(--po-accent) 86%, var(--po-text) 14%)`.
- Primary action foreground: `var(--po-text-inverse)`.
- Primary action border: none.

The dark theme primary blue has been visually approved for the current desktop
Git surface. Do not override it with the light theme blue.

## Desktop Git Sidebar

The Git sidebar is an operational tool surface. It should be dense, quiet, and
easy to scan.

- Main font size: `13px`.
- Small metadata font size: `13px`. Avoid introducing extra 11px labels.
- Regular weight: `520`.
- Strong weight: `650`.
- Row height: `30px`.
- Control radius: `6px`.
- Primary Git buttons use the theme blues above and have no border.
- Commit actions use a `+` icon, not a checkmark.
- Pull uses a downward arrow icon.
- Push and publish use an upward arrow icon.
- Empty states use normal, non-italic text and read `Empty`.
- Remote changes are expanded by default and the preview can scroll up to half
  the sidebar height.
- Git has two display modes in Appearance:
  - Simple mode is the default. It hides the separate staged section and makes
    `Stage & Commit` the primary local action inside `Unstaged Changes`.
  - Professional mode exposes `Remote Changes`, `Committed Changes`,
    `Staged Changes`, and `Unstaged Changes` as separate sections.
- `Committed Changes` owns `Push` and `Publish`. Do not show push actions inside
  `Staged Changes`.
- `Committed Changes` should show a clickable file preview list when local
  commits are waiting to push.
- History is a right-side main panel, not an expandable sidebar drawer. The
  sidebar only shows a compact `History` entry; the main panel shows the commit
  tree and the selected commit detail.
- Remote errors should appear in a dialog, not inline between sidebar sections.

## Git Error Dialogs

Git operation failures must preserve both a human summary and the raw Git
output.

- Show a concise user-facing summary first.
- Show `Raw Git output` in a monospace scroll region.
- Provide `Copy Prompt` for Codex or Claude Code repair workflows.
- The copied prompt must include the operation, workspace path, summary, and raw
  Git output.
- Never hide raw Git output behind a cleaned product message.
- Never suggest destructive Git commands such as reset, clean, or force-push
  without explicit risk explanation and confirmation.

## Button Principles

- Use primary buttons only for the next important action.
- Avoid bordered primary buttons in the desktop shell.
- Do not make primary buttons blue-gray. If the action is primary, use the
  theme blue. If it is secondary, use neutral controls.
- Keep icon size and stroke consistent across paired actions.
- Buttons in compact sidebars should not exceed the local row rhythm unless the
  surrounding design intentionally creates a larger callout.

## Maintenance

When a visual value is changed in CSS, update this document in the same change
if the value is intended to become product policy. If a value is experimental or
surface-local, keep it out of this document.
