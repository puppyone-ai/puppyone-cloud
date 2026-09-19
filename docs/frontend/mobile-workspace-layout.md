# Web workspace responsive architecture

Updated: 2026-09-19. Implemented in `frontend`; the desktop application is unchanged.
The second refactor replaces independent viewport breakpoints and percentage pane
widths with one constraint-based workspace allocation.

## Ownership

```text
ResponsiveWorkspaceProvider
├ WorkspaceViewport (keyboard / visible height, not horizontal layout)
├ WorkspaceLayoutProvider (stable store, no pixel Context broadcasts)
└ WorkspaceLayoutFrame (one host ResizeObserver, resolved CSS variables)
  ├ ProjectNavigationRegion (registers Projects preference and collapse intent)
  │ └ ResponsiveDrawer → WorkspaceProjectRail
  └ main [appContent ref]
    └ ProjectSessionProvider (identity = projectId)
      └ ProjectWorkspaceShell (stable Grid)
        ├ ProjectsHeader [header ref]
        ├ body [projectBody ref]
        │ └ DataWorkspaceSurface
        │   ├ FileNavigationRegion → WorkspaceFileColumn → FileExplorer
        │   ├ EditorViewport [editor ref] → EditorArea
        │   └ WorkspaceInspectorRegion → file version/sync/editor details
        ├ ProjectAuxiliarySidebar (session / retained runtime owner)
        │ └ WorkspaceAuxiliaryRegion (registers open intent / preference)
        │   └ ResizablePanel → retained Agent / Access content
        └ ProjectSettingsDialog (portal, modal)
```

`features/workspace/paneLayout.ts` is the only owner of workspace pane geometry.
It receives the host's content-box width, pane presence, user preferences,
minimum/maximum sizes and optional native display segments. It returns rendered
widths, docked/overlay presentation, resize maxima, Header placement and the active
right overlay owner. Local file inspectors participate in the same budget, even
when a local editor and Agent are both open.
`layoutStore.tsx` registers pane inputs without broadcasting pixel state into
project sessions. `WorkspaceLayoutFrame.tsx` observes the stable outer host,
subtracts safe-area padding and publishes the resolved CSS contract.

The frame measures itself, never a child whose width it has just allocated.
Unchanged measurements do not update the store. ResizeObserver handles host
resizing independently of browser viewport size; the resize event also refreshes
native segment topology. There is no debounce that makes panels trail a drag.

## Space policy

Web defaults remain Projects 220px (user collapse 56px), Files 220px, Agent 450px.
Files can be dragged from 220–480px, Agent from 300–800px, and Projects from
160–360px. Live maxima also protect the current document and other docked panes.
The document floor is 320px; if the entire available viewport is narrower, it
uses that full available width. Overlay sidebars have their own bounded sizes.

1. Preserve all pane preferences when they fit; spare space belongs to the document.
2. Once the document reaches its floor, reclaim only the needed deficit from
   Agent, then a local file inspector, then Files, stopping at each pane's minimum. Projects does not scale
   with the window.
3. If all docked minima cannot fit, Projects becomes a drawer first, then Files.
   Preserve the document/Agent pair while their minima fit.
A local inspector yields to a body overlay before Agent when both are open.
4. If even that pair cannot fit, Agent becomes a right overlay below Header.
   Files remains a left sidebar below Header; neither replaces the page.

These transitions derive from the current panes, not device names or a fixed
1024/900/640 classification. Closing Agent, changing routes, dragging Files or
collapsing Projects changes the budget. Necessary docked-to-overlay transitions
release a pane's space; they are explicit discrete presentation changes.

At the default widths with all three sidebars open, some resolved examples are:

| Host width | Projects | Files | Agent | Document |
| ---: | --- | --- | ---: | ---: |
| 1280 | docked 220 | docked 220 | docked 450 | 390 |
| 1210 | docked 220 | docked 220 | docked 450 | 320 |
| 1100 | docked 220 | docked 220 | docked 340 | 320 |
| 1060 | docked 220 | docked 220 | docked 300 | 320 |
| 1059 | drawer | docked 220 | docked 450 | 389 |
| 840 | drawer | docked 220 | docked 300 | 320 |
| 839 | drawer | drawer | docked 450 | 389 |
| 620 | drawer | drawer | docked 300 | 320 |
| 390 | drawer | drawer | overlay 358 | 390 underneath |

`preferredWidth` is user intent. `renderedWidth` is a temporary constrained
result. Passive resizing never writes width preferences or pane open flags.
Files persists only user resize actions; restoring space restores the preference.
Resizable components receive resolved widths and live drag bounds. They do not
choose another responsive presentation. Closing retains the Agent content plane;
ordinary visible resizing lets content reflow normally.

### Explicit pane motion

`usePaneMotion` separates user open/collapse intent from passive host resizing.
Only explicit toggles enable the 200ms width/slide transition. Host width or
segment changes cancel motion immediately, and reduced-motion users settle
without an animation or delayed allocation release.

During a right pane's exit, its allocation remains registered until the
transition ends (with a bounded fallback timer). This keeps its Header anchor,
docked/overlay mode and native segment placement stable while it slides away.
The closing surface is painted but inert; its content plane retains its width
so text and inputs do not squeeze. Local inspector content is retained only
through this exit; Agent runtime retains its existing session lifetime.
Rapid reversals cancel the old completion timer. Resize handles become available
again after a toggle settles. Projects rail width and navigation drawer slides
use the same intent-only motion boundary.

## CSS and interaction contract

`responsive-workspace.css` consumes frame data attributes and CSS variables for
pane geometry. It has no `45cqw` rule or independent pane media breakpoints.
Navigation buttons, Header span, backdrop and drawer positioning follow the same
resolved presentation that focus/inert/swipe hooks consume. File inspectors use the resolved body-level presentation and width; generic
resizable panels cannot override workspace regions through their media rules.

Header and editor toolbars use their actual container width. Local content,
dialog and history views retain independent responsive styles; those rules do
not assign workspace pane widths. Pointer capability controls tree touch targets
and always-visible actions independently of window width.

The project Header remains a single 46px row at every width, with a themed
bottom divider. Narrow containers collapse labels and parent breadcrumbs;
multi-control file editor actions move into a disclosure anchored beneath the
same row, retaining one mounted set of controls. There are no second or third
Header rows. When Files is active in overlay mode, Browse files supplies its
entry without a duplicate active Files link. Wide layouts retain inline tools.
The single-row layout was visually checked in light/dark themes at 1120, 600,
390 and 320px, including the compact editor control disclosure.

`window.viewport.segments`, where available, supplies native segment geometry.
The adapter intersects segments with the workspace's content box, including safe
areas; the allocator reserves the hinge and publishes segment sizes. A horizontal
pair places document left and Agent right. A vertical pair uses separate top and
bottom work areas. Unsupported browsers use the ordinary space policy. Native
segmentation is progressive enhancement and is active when Agent is open.

## State, focus and lifetime

- Project and file identity, editor drafts, saves and Chat streams are owned by
  their business session. No responsive route, viewport key, alternate content
  tree or changing Portal destination is introduced.
- Only frames and region chrome subscribe to pixel geometry. Focus/gesture
  consumers subscribe to their own discrete presentation. Stable children mean
  neither editor nor Chat runtime executes for unrelated layout updates.
- Projects/Files drawer open intent survives resize. Opening one closes the
  other; selecting an item, Escape, backdrop or outward swipe explicitly closes it.
- `useSidebarOverlay` uses explicit region refs and reference-counted inert locks.
  Projects is modal; Files and Agent keep Header available. Nested menus and
  dialogs retain keyboard priority. No ancestor traversal guesses the background.
- The auxiliary focus manager ignores a stored navigation-open flag while that
  navigation is actually docked. Presentation, not a stale device mode, determines
  which visible overlay owns focus.
- VisualViewport height/top adjustment, `dvh`, safe areas and the Markdown scroll
  snapshot adapter remain independent of the horizontal allocation.

## Verification

Pure geometry tests cover preference preservation, sequential compression,
minimum widths, absent/closed panes, a collapsed Projects rail, live resize
maxima, native segments and continuous widths across multiple preferences.
Component regressions cover retained editor/Chat identity and drafts, zero
unrelated runtime renders, overlay focus/inert, swipe and explicit close intent.

A repeatable browser fixture imports the actual frame, provider, CSS, Files
column and auxiliary region; only business content and project-list rows are
probes. It is outside Next routes and the production bundle:

```sh
./node_modules/.bin/vite --config tests/browser/vite.config.ts
```

Open `http://127.0.0.1:4174/tests/browser/workspace-layout.html` and choose
**Run browser geometry regression**. **Reset fixture** clears this isolated
origin's test width and reloads before a repeat run. The fixture measures actual
bounding rectangles across 26 host widths, not mocked CSS or stored preferences.
It additionally exercises 8 inspector widths and checks Header anchoring, document isolation, DOM identity, runtime render
counts, real keyboard resize handlers, preference restore and Agent close/reopen.

Browser fixture checks are not authenticated application end-to-end tests.
Physical iOS/Android keyboards, IME and fold/hinge behavior still need device
acceptance. No claim is made about measured frame times or a live SSE connection.

References: [ResizeObserver](https://developer.mozilla.org/en-US/docs/Web/API/ResizeObserver),
[content-based responsive design](https://web.dev/articles/responsive-web-design-basics),
[container queries](https://web.dev/learn/css/container-queries),
[native viewport segments](https://developer.mozilla.org/en-US/docs/Web/API/Viewport/segments),
[React state identity](https://react.dev/learn/preserving-and-resetting-state).

Validation on 2026-09-19: 25 files / 124 unit and component tests, TypeScript, theme audit and production build passed. Browser fixture: Safari, 26 Agent widths plus 8 file-inspector widths.

Follow-up motion regression on 2026-09-19: removed the blanket workspace
`transition: none` behavior for explicit toggles and retained outgoing surfaces
through their exits. 26 files / 130 tests, TypeScript and theme audit passed.
The browser fixture's **Run sidebar motion regression** samples actual entry/exit
frames at 1280px and 390px for Agent and Inspector, retained content width,
rapid reversal, resize cancellation, Projects collapse/expand and Files slides.
Safari passed this suite and the existing 26 + 8 geometry checks. Adding
`?run=all` to the fixture URL resets only that fixture origin's Files width and
runs both suites. The production Web Agent was also opened for visual inspection;
these checks do not substitute for physical mobile device acceptance.
