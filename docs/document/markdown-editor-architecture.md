# Markdown Editor Architecture

This document records the architecture and layout invariants for the PuppyOne
Markdown editor. Update it with any change that affects Markdown editing,
preview rendering, CodeMirror decorations, cursor placement, or editor layout.

## Runtime Shape

- Markdown files are raw text documents. The persisted source is the Markdown
  string, not a rendered HTML document.
- The shared editor implementation lives under `frontend/shared-ui`.
- The standalone Desktop app consumes its own vendored copy of the shared editor
  from `frontend/shared-ui`.
- The Markdown viewer has two modes:
  - `Live view`: one editable CodeMirror document with syntax-aware
    decorations and widgets.
  - `Source code`: raw Markdown source editing.
- `Live view` is not a second editable DOM tree. It must not maintain a
  separate HTML preview over or beside CodeMirror.

## Core Architecture Principles

These are the non-negotiable architecture rules for the Markdown editor:

- **Markdown source is the single source of truth.** The persisted document is
  the Markdown string. Rendered HTML, task checkboxes, table previews, and
  styled headings are projections of that source, never alternate document
  state.
- **CodeMirror owns editing.** CodeMirror owns the document model, selection,
  cursor, mouse coordinate mapping, keyboard navigation, IME composition,
  wrapping, scrolling, and height measurement. Feature code must work through
  CodeMirror transactions and decorations instead of maintaining a parallel DOM
  editor.
- **Live preview is a visual adapter.** Preview styling may hide syntax tokens
  or add widgets, but it must not move normal text out of CodeMirror's native
  text layer. Text that users read as document text must remain selectable,
  copyable, and navigable as CodeMirror text.
- **Geometry must be explicit and stable.** Any widget or decoration that
  changes visual geometry must have a clear contract with CodeMirror. Inline
  replacements need stable `coordsAt()` behavior. Block widgets need measured
  height that matches DOM height.
- **Interaction controls stay outside text baseline.** Controls such as task
  checkboxes, source/preview toggles, and embedded preview buttons must not
  influence the baseline or line height of adjacent document text.
- **Trust and rendering policy is separate from editing.** Local trusted HTML,
  safe/cloud HTML, source viewing, and unsupported HTML fallbacks are rendering
  policy decisions. They must not change the Markdown editing model or create a
  second editable document tree.

## CodeMirror Ownership

CodeMirror owns:

- the document model
- the selection
- mouse coordinate mapping
- scroll position
- line wrapping
- height measurement

Do not add custom click-to-position logic with `elementFromPoint`,
manual `Range` math, or external DOM pointer mapping. Cursor placement must go
through CodeMirror's native `posAtCoords` / `coordsAtPos` flow.

## Live Preview Decorations

Live preview renders Markdown syntax through CodeMirror decorations:

- Inline Markdown markers may be hidden with inline replacement decorations.
- Task/list markers may be inline widgets.
- Horizontal rules, fenced code blocks, and Markdown tables may be block
  replacement widgets.
- Source text remains the only persistence format. Widgets write back by
  dispatching CodeMirror transactions against the underlying Markdown ranges.

## Live Preview Editing Principles

These checks are mandatory for every live-preview Markdown feature. They are
not the whole architecture; they are the practical guardrails that enforce the
core principles above:

- **Do not break text coordinates.** A decoration or widget must not make
  CodeMirror's cursor, selection, copy, or mouse coordinate mapping diverge
  from the visible text. If a widget replaces source text or hides syntax, it
  must either be geometry-neutral or implement `WidgetType.coordsAt()`.
- **Do not move real text into opaque DOM.** Markdown source remains the editor
  document. Normal text, including task content, headings, and list content,
  must stay in CodeMirror's native text layer so drag selection, keyboard
  movement, IME, and copy stay native.
- **Do not let preview controls affect baseline or line height.** Interactive
  controls such as task checkboxes belong in marker space or in measured block
  widgets. They must not participate in the baseline of the surrounding text.

In plain terms, the three checks mean:

- **Line height / baseline check:** the visible text and the CodeMirror cursor
  must share the same vertical coordinate system. Styling a heading, list item,
  task item, or inline marker must not make the caret appear above or below the
  text.
- **Selection / copy check:** mouse drag selection, multi-line selection,
  keyboard selection, and copy must behave as if the user is editing normal
  Markdown text. A visual widget must not swallow text or make text impossible
  to select.
- **Markdown source check:** every preview action must map back to a concrete
  Markdown range. A task checkbox toggles `[ ]` / `[x]`; a table cell edit
  changes the table source; a source toggle only changes presentation. None of
  these create separate persisted state.

The practical split is:

- Inline syntax markers such as `# `, `**`, `_`, `~~`, link brackets, link
  URLs, quote markers, and task source prefixes may be visually hidden, but
  their replacement widgets must return cursor coordinates aligned to the
  current line's visible text.
- List bullets and task checkboxes may be rendered as widgets, but they must
  not own the adjacent content. The adjacent content remains editable
  CodeMirror text.
- Block-level rich preview, such as tables, fenced code blocks, trusted HTML,
  and horizontal rules, may use block widgets because they intentionally replace
  whole Markdown blocks.

## Breakout Blocks

The Markdown editor uses a constrained prose column for normal text. Some
structured blocks need more horizontal room:

- Markdown tables
- database-like views
- large embedded HTML surfaces
- wide code or data previews

These should use a **breakout block** pattern:

- Keep the prose column width unchanged.
- Let the block's own viewport expand within the editor container, usually to
  the right side of the prose column.
- Keep horizontal overflow inside the block itself with `overflow-x: auto`.
- Do not increase the overall editor document width or create a global
  horizontal scrollbar on the CodeMirror scroller.
- Compute breakout width from the editor container, not from the browser
  viewport, so the same component works in desktop panes, split views, and web
  layouts.

Tables currently follow this pattern: the table wrapper may expand beyond the
prose width, but the table still owns its horizontal scrollbar and the rest of
the editor keeps the normal Markdown text width.

## Wiki Links And Backlinks

PuppyOne supports Obsidian-style wiki links as a Markdown projection:

```md
[[Target note]]
[[folder/Target note]]
[[Target note#Heading]]
[[Target note|Readable label]]
```

The link graph also treats standard Markdown links to local Markdown documents
as document links:

```md
[Readable label](Target note.md)
[Readable label](folder/Target note.md#Heading)
```

The architecture follows the same source-first rules as the rest of the
Markdown editor:

- `[[...]]` syntax stays in the Markdown source. Live preview may hide the
  brackets and target portion when an alias is present, but it must not create
  separate persisted link state.
- Parsing is centralized in the Markdown link models. Tables, inline preview,
  live editor decorations, backlink indexing, and future backend indexers must
  use the same grammar instead of each implementing their own regex.
- The editor consumes a `MarkdownLinkGraph` interface. It can resolve a wiki
  target, open a resolved target, open a path, and return backlinks for the
  current document.
- The link graph provider is replaceable. The shared UI currently builds a
  session index from the loaded workspace tree and cached Markdown contents.
  A desktop full-vault indexer or cloud backend index can replace that provider
  without changing CodeMirror decorations or Markdown rendering.
- Backlinks are a read-only projection of indexed Markdown source. They are a
  graph capability, not default document chrome. Any visible backlink UI must
  live behind an explicit surface such as a side panel, command, or toggle; it
  must not be appended to the Markdown document by default.

Resolution order should be deterministic:

- exact path or root-relative path
- source-folder-relative path
- path with implicit `.md`
- unique title match after stripping `.md` / `.markdown`
- nearest same-title document when multiple title matches exist, marked as
  ambiguous so the UI can surface that the source link should be made more
  specific

Missing links should remain visible and copyable. They may be styled as missing
targets, but they must not throw rendering errors or block editing. Only links
that resolve to an internal document, or safe external links, should take the
pointer cursor and intercept a normal click. Missing internal links should keep
the normal text cursor so users can click into the source and fix the target.

## Inline Coordinate Protocol

Any inline decoration that hides or replaces Markdown source must use the shared
coordinate protocol:

- `coordsAt()` must return `top` and `bottom` from the nearest visible text
  rectangle in the same `.cm-line`.
- If the line has no visible text, fall back to the line's computed
  `padding-top`, `font-size`, and `line-height`.
- `left` and `right` may use the widget edge or the start of the real text,
  depending on whether the widget represents an inline marker or a marker-space
  control.
- CSS for inline widgets must not add vertical margins, dynamic height, or
  baseline-changing transforms to the surrounding text.

This is required because CodeMirror draws the cursor and selection itself.
Browser DOM may visually hide syntax tokens, but CodeMirror still needs stable
screen coordinates for those source positions. Empty `Decoration.replace({})`
is not enough once headings, line padding, list markers, or inline widgets are
involved.

## Cursor Offset Root Cause

In June 2026, the desktop Markdown editor had a cursor-placement bug where
clicking a visible line placed the caret several lines below the click. The
root cause was block widget layout, not Electron, scaling, fonts, or pointer
events.

The previous live preview CSS gave block widgets vertical margins, for example:

```css
.cm-md-hr-widget {
  height: 1px;
  margin: 18px 0;
}
```

`Decoration.replace({ block: true })` participates in CodeMirror's height map.
CodeMirror measures the block widget itself, but vertical margins on block
widgets are outside the measured box. The DOM was pushed down by the margins
while CodeMirror's height map was not. `posAtCoords` then mapped click
coordinates to lower document positions.

The bug was cumulative. Each horizontal rule added `18px + 18px = 36px` of
untracked vertical offset. A document with four horizontal rules showed about
`144px` of cursor offset after the fourth rule.

## Layout Invariants

These rules are mandatory for all Markdown live-preview widgets:

- Do not add vertical `padding` to `.cm-line` for decoration-only spacing unless
  hidden/replaced inline syntax in that line maps coordinates to visible text
  through `coordsAt()`.
- A CodeMirror block widget root must not use vertical `margin`.
- Visual spacing around a block widget must be represented inside the measured
  box, using `height`, `padding`, or an inner wrapper.
- Do not use hover/focus/click states that change the block widget's measured
  height unless the widget calls `view.requestMeasure()` after the change.
- Do not allow user-resizable widget DOM, such as `textarea { resize: vertical }`,
  unless CodeMirror is notified with `view.requestMeasure()`.
- Images or other async-loading content that can change measured height must
  call `view.requestMeasure()` on load and error.
- Prefer fixed or content-stable widget geometry for rendered blocks.

Current expected patterns:

- Horizontal rule widget: root has `margin: 0` and a fixed measured height; the
  1px rule is drawn inside that box.
- Code block widget: root has `margin: 0`; vertical spacing is root `padding`;
  visual background lives in an inner panel.
- Table widget: root has `margin: 0`; vertical spacing is root `padding`.
- Empty code-language controls may change opacity, but must not collapse or
  expand height on hover/focus.

## Regression Checks

Before merging Markdown live-preview layout changes:

- Use a Markdown fixture with headings, multiple horizontal rules, fenced code
  blocks, tables, images, lists, Chinese text, and long wrapped paragraphs.
- For visible text lines after every block widget, sample a point inside the
  line's DOM rect and call `view.posAtCoords({ x, y })`.
- Convert the returned position back with `view.state.doc.lineAt(pos)`.
- The returned line must be the clicked line.
- Compare `view.lineBlockAt(line.from)` screen position to the line DOM rect.
  Any delta must stay constant across the document; it must not grow after
  horizontal rules, code blocks, tables, or images.
- Verify all block widget roots have `margin-top: 0px` and
  `margin-bottom: 0px`.

Also run:

```bash
npm run shared-ui:test-markdown-html
npm run build
```
