# Product Visual System

This file captures the current Puppyone product UI rhythm used by Data,
Access, Home, Develop, and Workflow surfaces. New project pages should start
from these values before introducing local variants.

## Surfaces

- Page background: `var(--po-canvas)`.
- Panel background: `var(--po-panel)`.
- Hover row background: `var(--po-hover)`.
- Selected row background: `var(--po-selected)` plus a 2px accent bar when a
  persistent list selection is needed.
- Standard border: `var(--po-border)`.
- Quiet card/row border: `var(--po-border-subtle)`.
- Hover border: `var(--po-border-strong)`.
- Radius: 8px for cards and rows, 6px for controls.

## Type

- Page chrome title: 13px / 18px, weight 600.
- Card/list title: 12px / 16px, weight 600.
- Labels and controls: 12px / 16px, weight 500.
- Metadata: 11px / 14px, weight 500.
- Body copy inside panels: 12px / 16px, weight 400.
- Letter spacing is 0 except uppercase table headers, which may use the
  existing Develop log style.

## Geometry

- Page header height: 46px.
- Subsidebar width: 300px.
- Sidebar/list row height: 54-64px depending on secondary metadata.
- Provider avatar container: 30x30px.
- Provider logo inside avatar: 18x18px.
- Icon button: 30x30px.
- Text button: 30px height.
- Text input/select: 34px height.
- Dense table row: 34px minimum height.

## Provider Marks

- Prefer backend `icon_url`.
- Render provider logos at 18px, centered inside a 30px neutral hover-surface
  avatar container.
- Do not replace Google/Gmail/GitHub provider marks with generic database or
  document glyphs when a provider is known.

## Status Indicators

Status indicators are the small green/yellow/red/gray lamps or semantic markers
used to show runtime, authorization, or action-needed state: access scope
status, connector state, OAuth authorization state, sync state, server
connection state, project health, Needs action rows, and similar live status.

- Always render status lamps through `frontend/components/ui/StatusDot.tsx`.
  Use `StatusDot` for dot-only UI and `StatusIndicator` for dot + label.
- Canonical lamp size is 6px. Do not hand-roll 4px/5px/7px/8px variants.
- Status lamps are flat. Do not add shadow, glow, blur, ping, or pulse effects.
- Status lamps use semantic tones only:
  - success: `active`, `ready`, `connected`, `success`, `completed`, `online`.
  - accent: `syncing`, `processing`, `running`, `loading`.
  - warning: `pending`, `warning`, `paused`, `queued`, `mixed`.
  - danger: `error`, `failed`, `danger`, `blocked`, `needs attention`.
  - muted: `inactive`, `disconnected`, `stopped`, unknown, or empty.
- If the dot sits on top of an avatar or provider mark, the caller may add a
  local border for separation, but the dot itself still comes from `StatusDot`.
- Timeline/action-needed markers use the same square geometry when they encode
  state. Required-field markers should also use the same 6px rounded-square
  shape, even when they are implemented inline inside compact forms.
- Do not use status lamps for non-status decoration. Count badges, selection
  checkmarks, icon internals, loading dots, progress bars, and onboarding
  decoration may keep their own visual treatment.

## Workflow Page

- The Workflow page is a project-level resource surface, not Access.
- The left subsidebar is an index of workflows.
- The main detail shows exactly two endpoint nodes: `Source -> Target`.
- Trigger is a small clock control before Source, not a workflow node.
- Source-specific sync policy lives inside Source.
- Target-specific write policy lives inside Target.
- Recent runs use the same dense table rhythm as Develop logs and GitHub sync
  log tables.

## Workspace Shell

The Cloud workspace shell follows the Desktop shell contract. Desktop is the
visual authority for persistent navigation and titlebar actions; Cloud adapts
the same grammar to the browser without inventing a second administration-style
navigation system.

### Project rail

- Expanded width is 220px, resizable from 160px to 360px.
- Collapsed width is 56px: 12px gutter + 32px control + 12px gutter.
- The rail starts with a compact control band containing account identity,
  Settings, and the project-rail collapse action. Expanded mode keeps the three
  controls on one 46px row; collapsed mode stacks them at the top.
- Account, organization, Team, Billing, and Templates actions open from that
  avatar. Do not repeat the avatar in the footer.
- Project rows are 32px high with 1px block margins, a 6px radius, 6px
  icon-to-label gap, and 14px regular-weight labels.
- Expanded Cloud projects use the canonical 15px Cloud mark at 1.8 stroke.
  Collapsed projects add an identity badge so repeated Cloud marks remain
  distinguishable.
- Current-project state uses a quiet neutral backplate. Do not use blue icon
  accents or bold labels to carry selection.
- `Create new` is another quiet rail row with a 14px Plus mark.
- The rail has no utility footer. Version information belongs in
  Settings/About.

### Project titlebar

- The titlebar uses the same `--po-canvas` surface as the editor and has no
  bottom divider. Hierarchy comes from the two vertical sidebar boundaries;
  the titlebar should recede into the document surface.
- The reading order is project/file identity, primary project view, then
  right-side workspace actions. Keep the `Files` / `Git` / `Access` switch
  immediately after the current name instead of centering it independently.
- `Files`, `Git`, and `Access` are the three primary project views. They
  are a mutually exclusive switch and replace the center workspace content
  through stable project routes. Inactive views are icon-only; the selected
  view expands to icon plus label.
- Chat is the trailing titlebar action. Closed state is icon-only; open state
  expands to icon plus `Chat` while the shared right sidebar compresses the
  main workspace.
- Shell actions are icon-first, borderless controls: 24px high, 32px minimum
  width, 5px radius, and 3px gaps.
- Hover uses `--po-hover`; pressed/open state uses `--po-selected`.
- Use a vertical 18px semantic divider between ordinary project actions and
  controls that toggle the right sidebar.
- Chat uses the canonical Desktop Agent conversation outline.
- Tooltips and accessible names carry the text labels; the resting titlebar
  stays visually compact.
- Chat and contextual tools such as per-file history or connector detail use
  the same top-aligned auxiliary-panel component and seamless canvas treatment.

### File rail

- The file rail is the secondary sidebar and uses the same 32px row height,
  14px regular-weight label, 6px radius, and neutral selection grammar as the
  project rail.
- Do not render a synthetic `Root` row. The project itself is already the root
  context in the titlebar; repeating it consumes space without adding meaning.
- Root-level creation is a quiet `Create new` row immediately after the file
  tree. It opens the shared create menu, including new-file creation, rather
  than introducing a second create workflow.
- Root-level drag/drop remains attached to the file-rail surface even though
  the visual `Root` row is absent.

### Navigation behavior

- The project list is navigation, not a landing page. `/home` resumes the
  last accessible project, falling back to the most recently updated project.
- The project rail remains present inside the workspace. Files are the second
  pane, content is the center pane, and the right edge is one mutually exclusive
  auxiliary-panel slot.
- Files, Git, and Access switch the primary center surface. Chat alone is a
  persistent titlebar action; contextual details may also use the auxiliary
  slot when launched from within a primary view.
- The auxiliary panel is an inline sibling of the workspace, not an absolute
  overlay. Opening it reduces the available editor width, its own 46px header
  occupies the right segment of the titlebar row, and one continuous vertical
  divider separates it from the workspace from top to bottom. Resizing the
  panel reflows the editor in real time, matching Notion's side-panel behavior.
- Team, Billing, Templates, project settings, and integration administration
  are settings/account destinations rather than persistent primary rail items.

### Navigation performance

- A primary-view press updates the selected titlebar control immediately;
  route and data loading happen after that acknowledgement. Never make network
  completion the prerequisite for showing the user's selection.
- Prefetch the sibling `Files`, `Git`, and `Access` route chunks after the
  current view paints, and refresh that prefetch on pointer or keyboard focus.
- Closed auxiliary panels must not mount heavy runtimes or start their data
  hooks. Chat is code-split and first mounted on demand; after its first open it
  may stay mounted to preserve conversation state while the panel is toggled.
- Multiple panes reading the same project resource share one canonical SWR
  key. In particular, the explorer and folder content panes share path-listing
  data, and the global activity stack makes one active-only feed request before
  splitting items by kind in memory.
- Avoid per-row eager fan-out. Git commit files are collapsed by default except
  for the first file, so version content is fetched only when a diff is opened.
  Derived Needs-action data must reuse the already loaded history payload.
