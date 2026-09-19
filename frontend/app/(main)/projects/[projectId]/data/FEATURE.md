# Data Feature Contract

This file records product and engineering agreements for the Data page.
When a Data sidebar or Data workspace interaction changes, update this
contract in the same patch.

## Sidebar Create Flow

- Directory read failures must never be represented as an empty directory.
  Backend `ls/tree` errors should surface as request failures so SWR keeps the
  previous canonical cache; the sidebar may show a load-error row only when no
  prior listing exists.
- Clicking a sidebar folder or Root `+` opens the shared `CreateMenu` anchored
  to that exact row. The `+` itself does not immediately create a folder.
- The regular create menu is scoped to the clicked parent folder. `Folder`,
  `Markdown`, `JSON`, upload/import, and connector actions must target that
  parent unless the user explicitly picks another destination in a modal.
- Choosing `Folder`, `Markdown`, or `JSON` from the scoped create menu creates
  a single pending row in that folder.
- Folder pending rows are closed folder rows. They must not auto-expand.
- File pending rows use the correct file glyph for their type.
- The pending row label is type-specific loading copy (`Creating folder`,
  `Creating Markdown`, `Creating JSON`) rather than the final item name.
- The pending row shows a subtle inline loading indicator beside the loading
  label: three dots that bounce vertically. Do not place the pending loader in
  the row action area where it can read as a `...` menu.
- The pending row is inert: it does not navigate, expand, accept drops, or show
  row actions.
- After the backend create succeeds, the pending state is removed and the row
  displays the real folder name from the canonical directory cache.
- If the backend definitively rejects the create, the optimistic row is removed
  and the parent directory cache is refreshed.
- If the request fails with an ambiguous network/timeout error, keep the
  optimistic row pending and verify against the canonical directory listing.
  Remove it only after the server is reachable and confirms the folder is
  absent; clear pending when the canonical folder appears.

## Sidebar Folder Rows

- Folder rows in the sidebar are disclosure controls first.
- Sidebar tree folder rows use the existing line-chevron disclosure marker: the
  collapsed state points right and the expanded state points down. This must
  reuse the same folded-line visual language that appears on hover/disclosure
  controls, not a newly drawn filled triangle or arrow.
- Sidebar row text is 13px. The disclosure marker should visually match that
  text height, using a 12px marker by default rather than a tiny 10px icon.
- A normal click anywhere on a folder row toggles expand/collapse.
- Folder row click must not navigate or activate the main workspace.
- File rows remain the navigation/open action.
- Row action buttons such as `+` and `...` are separate controls and must not
  also toggle the folder row.
- Hidden hover actions must not reserve the full action layer before hover.
  Row text must still reserve one fixed slot for the row object's `...` menu
  whenever that menu exists, plus one persistent Access slot when Access is
  configured. Extra hover actions such as `+` may float above the right edge,
  but the primary object menu must never overlap the row label or create a
  large empty reserved band.
- Root is the fixed workspace root, not a normal collapsible folder row. It is
  always expanded, does not render a disclosure marker, and clicking the Root
  row itself must not collapse the tree or navigate the main workspace.
- Root is rendered outside the scroll container, above the scrollable tree, so
  the scrollbar starts below Root and Root never appears as part of the scrolled
  content. Its label should be visually subdued because it is context, not a
  selectable item.
- Root's fixed layer must use the same canvas background as the explorer pane,
  avoid a separate tinted row block in the normal state, and reserve the same
  right-side gutter as the scroll container so Root actions align with row
  actions below it. It may use only a subtle bottom divider plus explicit
  transient states such as drop target, menu-open, or Access highlight.
- Root-level children render as top-level tree rows without a redundant
  root-rail line at the far left. Child folders still draw their own rails
  once expanded.
- Sidebar tree rails follow VS Code-style vertical indent guides. Do not draw
  horizontal branch elbows, and do not stop a parent guide at the last sibling
  row when that row is expanded; the guide must continue through the expanded
  subtree, including loading, error, and `Empty folder` meta rows.
- Root row actions (`...`, `+`, and configured Access) stay visible because
  Root has no hover disclosure marker and cannot be selected elsewhere.
- Root and folder rows must not use selected/active styling just because the
  current route is that folder. Folder routes may expand the tree path, but
  selected styling belongs to files or explicit transient states only.
- Expanded empty folders, including Root, must render an `Empty folder` meta row
  instead of leaving the subtree visually blank.

## Shared Icon Primitives

- Expand/collapse affordances use `TreeDisclosureMarker`. Do not hand-draw
  local chevrons, filled triangles, or one-off folder disclosure SVGs.
- Small row/menu/file-type glyphs use `FileGlyphIcon`. That includes folder,
  Markdown, JSON, and other compact file/resource icons in sidebars, menus,
  trees, and list-like views.
- Once folder rows use the quieter disclosure marker, file glyph colors carry
  the sidebar's visual affordance. Keep Markdown in a muted denim/steel-blue
  range, JSON warm/amber, and generic files green-gray so clickable files do not
  collapse into the same gray as row chrome such as `...`, tree rails, and
  disabled text. Avoid saturated system-link blue for Markdown in the warm
  sidebar.
- Large preview/card artwork uses `FilePreviewIcon`. Keep this separate from
  disclosure markers so grid cards can still read as tangible files/folders
  while tree rows read as expandable controls.

## Main Workspace Empty State

- Root and folder routes are not file preview targets. When no file is active,
  the main workspace must show a neutral no-file-selected state instead of a
  blank canvas or a selected folder preview.
- The no-file-selected state should name the state plainly (`No file selected`)
  and avoid implying the current folder is empty. When the current workspace or
  folder already has items, this state stays quiet: no primary create button and
  no upload prompt in the center of the editor pane.
- Empty root and empty folder views are separate from no-file-selected. They
  should say `This workspace is empty` or `This folder is empty` and provide two
  balanced actions: create a Markdown note and upload files.
- Do not show JSON or every create-menu option in the empty state;
  structured-data creation stays in the sidebar/current-folder create menu.
- This state is different from the empty-project onboarding surface. Empty
  project onboarding may hide the explorer and focus on bringing in first
  context; no-file-selected keeps the explorer visible because the project tree
  already exists.
- Folder/root clicks in the sidebar must not change the URL just to show this
  state. The state is for direct folder URLs, breadcrumb navigation, and the
  default root route.

## Folder Commands

- The project-level `Access` button belongs at the far right of the Data
  header and is not tied to the currently focused file or folder. It must open
  modal-owned Access surfaces, not route the user into the right sidebar:
  one or more existing access points opens the overview modal, and zero access
  points opens the create modal.
- The Data header `...` menu is file-only. Root and folder routes must not show
  a header object-command menu; their object commands live on the sidebar row.
- Sidebar folder rows have two creation/command hover actions at most: `...`
  for object commands and `+` for creating children. Configured Access may add
  one persistent Access status/action button.
- When both `+` and `...` are present, render `+` first and `...` after it so
  the single reserved object-menu slot lines up with the `...` menu instead of
  being stolen by the create button. Configured Access remains to the right of
  both.
- These hover actions are positioned as an absolute right-side action layer
  inside the row. They do not participate in the row text flex layout and must
  not make folder names resize on hover.
- Tree rows must keep a full-width row box regardless of nesting depth. Depth
  changes indentation and tree guide positions, not the row's right boundary or
  the Access/action alignment column.
- Every tree wrapper, subtree motion wrapper, and subtree content wrapper must
  also be full width. A full-width row inside a shrink-wrapped subtree is still
  broken: hover backgrounds, `...`, and Access buttons will stop short of the
  sidebar edge.
- The row `...` menu operates on that exact sidebar item without selecting it.
  It may contain `Rename`, `Expose as...` / `Manage Access`, `Download`, and
  `Delete`.
- The row `...` menu must never open commands for the current editor file,
  breadcrumb folder, or another hovered row. It owns the object represented by
  the row that rendered it.
- `Rename` and `Delete` for folders live in the sidebar row menu because folder
  rows are disclosure controls, not selected editor targets.
- `Download` from a folder row downloads that folder/subtree. `Download` from a
  file row downloads that file.
- Access creation/Expose for a specific folder lives in the row object menu for
  that exact sidebar folder. Do not expose the current folder through a header
  `...` menu.
- Configured Access is a persistent status/action inside the row itself. Show a
  lightweight Access icon button at the far right of the row action layer, to
  the right of `+`, so users can see which folders already have Access without
  overpowering the project-level header Access button.
- Folders without configured Access do not show an Access button, rail marker,
  hover bubble, tooltip-like card, numeric badge, or extra right-side strip.
- The top-level header Access button stays visible as the discoverable
  project-wide access surface. It should be visually stronger than sidebar
  status/action icons, using the product's active Access green through the
  shared `--po-access-*` semantic tokens, but it is not the selected-folder
  command menu.

## Create Menu Modes

- `CreateMenu` has two product modes:
  regular create mode and access-only mode.
- Regular create mode is opened by the sidebar/root `+` or by the current
  folder view create affordance. It may show folder/file creation, uploads,
  imports, SaaS connectors, and integration entry points.
- Access-only mode is opened by `Expose as...` from a folder row. It must show
  only Access-related choices such as AI chat/agent, MCP, sandbox, or other
  access wrappers. It must not show generic file creation.
- The create menu is a transient chooser. Long forms, validation, and actual
  Access configuration belong in modal-owned flows, not inside the sidebar.

## Folder Upload And Git Directories

- Folder upload is a context import, not a Git repository migration.
- The Data `+` menu must expose one upload entry, not separate `Upload files`
  and `Upload folder` rows. Choosing upload opens the shared import dialog;
  that dialog owns the concrete choice between folder upload, file upload, and
  drag/drop.
- In the import dialog, `Upload folder` is the primary action and `Upload files`
  is secondary because Puppyone's context import is normally folder-shaped.
  Folder upload must stay discoverable without bloating the top-level create
  menu.
- The import target line (`Import to <folder>`) is contextual metadata, not a
  form control. Render it as plain text above the drop zone, without a bordered
  chip/frame.
- After files are selected, the import dialog must preview the accepted upload
  as a folder tree, not a flat path list. Do not show `Add folder` / `Add files`
  controls in the selected-state preview; that state is for review and import,
  not for turning the dialog into a file basket.
- Upload preview folder rows may keep a folder-shaped file-transfer vocabulary,
  but it should not redefine the Data sidebar's disclosure marker. Nested rows
  must show visible tree rails/branch lines rather than relying on whitespace
  indentation alone.
- File import UI is module-owned under `frontend/components/file-import/`.
  `frontend/components/FileImportDialog.tsx` is only a compatibility export.
  Keep source selection (`FileImportSourcePicker`), upload preview
  (`FileImportPreviewTree`), policy messaging (`FileImportPolicySummary`),
  selection/policy state (`useFileImportSelection`), and pure helpers
  (`previewTree`, `fileStats`, `format`) separated so future overwrite
  preflight, empty-directory manifests, and large-import job handoff can be
  added without turning the dialog shell back into a monolithic component.
- Uploading a folder must not automatically follow `.git/`, import hidden Git
  objects, or graft the local repository's commit history into Puppyone's
  version history.
- Folder traversal must prune hard-blocklisted directories such as `.git/`,
  `node_modules/`, `dist/`, and `build/` before walking their descendants.
  Do not recursively enumerate these folders and filter afterward; large repos
  can freeze the browser before policy evaluation finishes.
- When a selected or dropped folder appears to contain a `.git/` directory, the
  upload UI should warn that Git history is skipped by folder upload and point
  users to `Start with Git` / the Git protocol if they want to preserve full
  repository history.
- Default-blocked paths are a hard skip, not a user override. The UI must not
  show an `include blocklisted files` checkbox because the backend rejects those
  paths as defense in depth.
- The default result of folder upload is one Puppyone version commit that
  represents the current file snapshot after upload filtering.
- Folder upload must enforce hard limits after filtering: at most 5,000
  accepted files, at most 1 GB accepted bytes per batch, and at most 100 MB per
  accepted file. Ignored `.git/`, `.gitignore` / `.puppyignore`, hidden, and
  blocklisted files do not count toward the batch file/byte limits because they
  will not be uploaded.
- Initial empty-workspace upload and later in-workspace upload share this Git
  directory policy. The difference is only product context: before
  initialization, the upload creates the workspace's first content commit and
  should keep the `Start with Git` alternative visible; after initialization,
  the upload imports into the current target folder and should not redirect the
  user away from the current Data workflow.
- Browser directory selection should prefer the File System Access API when
  available so Puppyone owns the confirmation/preflight surface. The
  `input[webkitdirectory]` fallback may remain for unsupported browsers, but it
  should not be the primary Chromium path because it adds an extra browser-owned
  confirmation step.

### Folder Upload Known Gaps

- Empty folders are not preserved today because the browser upload pipeline
  submits a flat `File[]` and no directory manifest. Before calling folder
  upload "complete", add an explicit empty-directory representation or product
  decision that empty directories are intentionally ignored.
- Duplicate target paths are not surfaced clearly enough. If two selected files
  resolve to the same Puppyone path, preflight should tell the user which file
  wins instead of silently relying on last-write-wins behavior.
- Overwrite/conflict preflight is still missing. Before upload, the UI should
  detect when accepted files will replace existing workspace paths and ask for
  confirmation or show a clear overwrite summary.
- Browser upload is not the final large-ingestion architecture. It is acceptable
  for small/medium user-guided imports, but durable bulk ingestion should route
  through Git protocol, CLI/desktop sync, or backend-owned import jobs that
  survive tab close, network drops, and process restarts.
- Upload policy needs frontend fixture coverage, not only backend unit tests.
  Fixtures should cover repo folders with `.git/`, `node_modules/`, ignored
  files, hidden files, empty files, empty folders, duplicate paths, and batches
  over file-count / byte limits.

## Sidebar Tree Motion

- Folder subtree expand/collapse should keep the smooth vertical content motion:
  children are clipped in/out by measured height while surrounding rows are
  pushed by layout.
- The line-chevron marker rotates in sync with the subtree state, but the
  important motion is still the subtree content: children are clipped in/out by
  measured height while surrounding rows are pushed by layout.
- Open ancestors should settle back to `height: auto` after their own transition
  so nested folder expansion pushes outside siblings continuously instead of
  serializing inner and outer movement.
- Long term, the cleanest tree-motion architecture is a flat visible-row model:
  derive the rendered rows from expansion state, then animate inserted/removed
  row ranges from one layout owner. Do not add more independent recursive
  animation wrappers unless there is a clear local reason and it is tested on
  deeply nested folders.

## Data Route Navigation

- Selecting a file changes the URL and editor target, but must not remount or
  refetch the left explorer sidebar.
- The explorer sidebar lifecycle is driven by project/content refresh events,
  not by ordinary file selection.
- Sidebar, grid, Miller-column item, and Data breadcrumb clicks should update
  the canonical Next URL through the workspace navigation adapter. The Files
  layout owns the stable workspace and explorer, so path navigation does not
  remount the file tree. Do not maintain a second client path or call native
  history from the Files feature.

- Browser back/forward must still restore the Data route state from the URL.

## Workspace Views

- Sidebar tree, grid, list, and Miller/Finder-style views share the same route
  and node-action controller. Do not fork create, rename, delete, download, or
  Access behavior per view.
- In every view, folders are navigational/disclosure objects and files are
  editor targets.
- Sidebar tree: folder row click toggles expansion only; file row click opens
  the file editor.
- Sidebar Root is fixed open. Clicking Root must not toggle, change the URL, or
  replace the right-side main surface.
- Grid and list views: folder item click navigates into that folder; file item
  click opens the file editor. Their per-item `...` menus operate on the exact
  card/row item.
- Miller/Finder-style view: folder item click advances/navigates the folder
  column; file item click opens the file editor. It should not create a second
  action model separate from the shared Data actions.
- The visible create affordance in any view opens the shared create menu scoped
  to the current folder, except sidebar row `+`, which scopes to that exact row.
- Empty folder states are view-specific presentations of the same fact: each
  view should show an empty state instead of a blank surface.

## Markdown Editing

- Markdown files opened from Data default to `Live view`.
- Markdown offers exactly two user-facing modes: `Live view` and `Source code`.
  Do not add a separate `Read only` Markdown mode.
- Dirty editor save belongs in the Data header action area, not as a centered
  overlay that blocks the document. The dirty `Save changes` state should read
  as a real high-contrast action button; `saving` and `saved` may remain softer
  status chips.
- Raw Markdown text remains the file's source of truth. Source-code edits must
  preserve unrelated whitespace, list markers, and blank lines; Live-view edits
  may serialize through the semantic editor after the user edits there.
- The detailed Markdown editor contract lives at
  `frontend/components/editors/markdown/FEATURE.md`; update it with any
  Markdown editor behavior change.

## Access Panel Overview

- The Access overview represents project-wide active access points, not files.
- The list heading is `Active access points`; do not use engineering language
  such as `Access scopes` in user-facing copy.
- Each overview row must stay compact, preferably two information lines:
  scope/folder name with active connector chips, then path with active connector
  summary, plus a chevron that makes the row's drill-down behavior clear. Do
  not show permission labels such as `Read & Write` or redundant object labels
  such as `Scope` in the row.
- The Access sidebar overview uses at most three text sizes: `13px` for titles
  and section labels, `12px` for body/path/connector summaries, and `11px` for
  compact connector chips or other metadata. Keep weights to the shared
  hierarchy (`600`, `500`, `400`) instead of ad hoc per-component styling.
- Clicking an access point row opens the access point detail page. It must not
  navigate the file tree.
- The Access overview's create action lives below the active access-point list
  as a list-shaped CTA (`New access point`). Do not place a large create button
  in the modal header; it competes with the overview title and makes one-scope
  projects hide the create path behind the quick modal.
- The overview create CTA should look like the user's next step: a prominent
  dashed-outline access row, not a filled success state and not a weak secondary
  status chip. The dashed border communicates "create a new thing here"; filled
  green is reserved for already configured Access actions.
- Configured Access indicators in the sidebar and overview use the same
  `--po-access-*` identity so users do not have to decode blue, black, and
  one-off greens as separate concepts.
- Access colors are centralized: components should reference semantic Access
  tokens (`--po-access-action`, `--po-access-active-*`) rather than hard-coded
  greens. Those semantic tokens derive from the product theme's active green.
- The overview modal itself should not mark Root or any other scope selected by
  default. It is a project-wide list, not a detail view.
- The sidebar may provide create entry points, but it must not own a second
  create form, loading state, or validation path.
- The Access create modal is the original two-pane create flow: the left pane
  browses Files and chooses a folder boundary, and the right pane shows folder
  details plus included/optional methods before the footer `Create access`
  action submits.
- The Access create modal's left pane must render Files as an inline expandable
  folder tree, not as a single-level browser with breadcrumbs. Its structure
  language should match the upload preview tree: folder rows expand in place,
  files may appear as muted leaves, and only folder rows expose the choose
  action.
- The Access create modal folder picker must follow the Data sidebar's tree
  grammar: line-chevron disclosure markers, 30px rows, 16px tree indents,
  visible tree rails, and no separate circular select button. Clicking a folder
  row selects it; the left disclosure marker expands or collapses it.
- The top tree row in the Access create modal is labeled `Root`, not `Files`,
  because users are choosing a boundary inside the project root. Folder rows use
  a compact check/radio-style selector for `Choose`; do not use a `+` icon there
  because `+` means create/add rather than select.
- Adding a SaaS/integration connector from an Access scope uses the shared
  integration-create modal. It must not replace the Access sidebar body with a
  `sync_create` page.

## Active vs Status

- Active/selected styling has one source of truth: route or explicit selection.
- Creating, syncing, importing, access hover, and other transient states are
  status feedback, not selection.
- A status row must not reuse active selection styling in a way that makes two
  sidebar elements look selected at once.

## Event Boundaries

- Sidebar row actions own their pointer/click isolation. A click on `+`, `...`,
  or a menu item must not also activate the row underneath it.
- Menu triggers such as `...` should stop propagation without preventing the
  browser's default pointer/click sequence. Preventing default on pointer down
  can make the menu fail to open in some event paths.
- Popover menu items must stop pointer/click propagation at the menu boundary.
- Row menus must render through a body-level portal. The explorer tree uses
  animated wrappers with `overflow: hidden` and transforms; inline fixed menus
  can be clipped or trapped by those ancestors, especially for nested rows.
- Callers should not need to remember ad hoc `stopPropagation` for standard
  row actions.
