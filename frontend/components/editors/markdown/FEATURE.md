# Markdown Editor Feature Contract

This file records product and engineering agreements for the Markdown editor.
Any change to Markdown editing, preview, save behavior, or mode defaults should
update this contract in the same patch.

See also `docs/document/markdown-editor-architecture.md` for the CodeMirror
live preview architecture, cursor-coordinate invariants, and the June 2026
block-widget margin root cause.

## Source Of Truth

- Markdown files are raw text assets. The exact source text is the canonical
  document.
- A normal one-line edit must produce a one-line textual diff unless the user
  explicitly asks for document formatting or semantic rewriting.
- Editor components must not silently normalize Markdown syntax, whitespace,
  list markers, blank lines, heading spacing, table layout, or any other source
  text outside the user's explicit edit.
- The backend write API stores the bytes it receives. It must not be relied on
  to recover raw Markdown once the frontend has already serialized a rewritten
  document.

## Editing Modes

- Markdown has exactly two user-facing modes in the Data page: `Live view` and
  `Source code`.
- Markdown files opened from the Data page default to `Live view`.
- `Source code` is the raw-text editing path and may write exact source text to
  `onChange`.
- `Live view` is editable for editable Markdown files. It uses the same
  CodeMirror text document as `Source code` and writes the exact text after the
  user edits in that mode.
- Do not add a separate `Read only` Markdown mode. File-level read-only state is
  controlled by the viewer's `editable` / `readOnly` props, not by a user-facing
  Markdown mode.
- Live view must not parse and serialize Markdown behind the user's back.
  Switching modes by itself must not write or mark the draft dirty.
- Live view must remount on document identity changes so CodeMirror never
  carries one file's editor state into another Markdown file.

## Save Boundary

- `onTextChange` receives the raw Markdown text from either `Source code` or
  `Live view`.
- Manual save commits the current raw text draft. It must not perform hidden
  parse/stringify round trips.
- Local drafts must preserve the exact string they were given, including blank
  lines and trailing whitespace.
- Switching view modes must not mark a Markdown file dirty by itself.
- Opening a Markdown file and saving without editing must be a no-op.

## CodeMirror

- Markdown editing is CodeMirror-first. CodeMirror owns the document, selection,
  scroll container, mouse coordinate mapping, and update lifecycle.
- Live view follows the same architecture class as Obsidian Live Preview: it is
  one CodeMirror editor with syntax-aware decorations, not a rendered preview
  layered beside or above a text editor.
- Live view may fold Markdown source tokens or replace rendered blocks only
  through CodeMirror decorations / widgets / atomic ranges. Headings, links,
  emphasis, tasks, horizontal rules, fenced code blocks, images, and Markdown
  tables must all remain inside CodeMirror's measured document flow.
- Live view must not change ordinary line layout on hover, click, focus, or
  cursor entry. Inline Markdown markers may stay folded so CodeMirror's native
  coordinate mapping sees the same text flow before and after pointer placement.
- Raw Markdown syntax editing belongs to `Source code` mode. Do not reintroduce
  selection-triggered full-line source reveal for ordinary body text, because it
  changes measured text flow after the pointer coordinate has already been
  resolved.
- Rendered block widgets such as Markdown tables and fenced code blocks must not
  fall back to full raw source on hover, click, focus, or cursor entry. They keep
  their rendered surface and provide local widget editing that writes back to the
  underlying Markdown through CodeMirror transactions.
- Do not add custom click-to-position, `elementFromPoint`, or external DOM
  pointer mapping for Markdown placement. Cursor placement must be CodeMirror's
  native coordinate mapping against its own content DOM.
- Block replacement widget roots must not use vertical margin. CodeMirror's
  height map does not account for block-widget margins, so each margin creates
  cumulative `posAtCoords` cursor offset after the widget. Put spacing inside
  the measured box with height, padding, or an inner wrapper.
- Any widget whose measured height can change after initial render, including
  async images or interactive controls, must call `view.requestMeasure()` after
  the change. Prefer stable-height widgets when possible.
- Live view must not maintain an alternate editable DOM tree or a separate HTML
  preview surface.
- Source text remains the only persistence format. Markdown preview styling must
  not introduce parse/stringify round trips.

## Regression Checks

- A Markdown fixture with nested lists, blank lines, code fences, and Chinese
  text should survive open -> save with byte-for-byte equality when no edit is
  made.
- Editing one line in source mode should change only that line in the produced
  diff.
- Switching between `Source code` and `Live view` should not rewrite content or
  mark the draft dirty.
- Editing in `Live view` should preserve raw text semantics and should be
  tested with cursor placement across headings, lists, code fences, tables, and
  wide blank editor areas.
