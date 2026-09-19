# PuppyOne Color Mode Architecture

Status: implemented for the shared theme contract; surface migration remains ongoing
Owner: Frontend
Scope: Light mode, dark mode, and system appearance support for the PuppyOne web app
Last verified: 2026-09-19

## 0. Implemented Cross-Product Contract

Cloud and Desktop share one visual contract even though they ship from separate
repositories. The Desktop `default-neutral` theme is the visual reference. Cloud
owns its consumable copy in
`frontend/shared-ui/src/styles/tokens.css`; `frontend/app/globals.css` imports that
file and must not maintain a second palette.

The shell anchor colors are final rendered values, not intermediate colors that a
feature may remix:

| Role | Light | Dark |
| --- | --- | --- |
| Main canvas | `#fafafa` | `#1a1a1a` |
| Header | `#ebebeb` | `#202020` |
| Sidebar | `#ebebeb` | `#202020` |
| Raised panel | `#ffffff` | `#222222` |

In particular, header and sidebar must consume `--po-header` and `--po-sidebar`
directly. Do not derive them again from `--po-surface-chrome` with `color-mix()`:
that creates a warm/yellow gray in light mode and breaks pixel parity with
Desktop. Product components consume semantic tokens only. Brand colors are also
named tokens; raw brand literals do not belong in feature components.

`next-themes` owns Light / Dark / System selection and the root `.dark` class.
`npm run theme:audit` enforces token usage and verifies that every used product
token is defined by the shared contract. Visual acceptance covers the home shell,
sidebar, settings appearance dialog, and a project workspace in both resolved
modes.

## 1. Why This Needs an Architecture

PuppyOne's current dark UI is visually strong, but the implementation is not yet a theme system. Colors are distributed across React inline styles, Tailwind arbitrary values, CSS files, SVG fills/strokes, Monaco themes, Milkdown styles, JSON editor overrides, and page-specific token files.

Adding a Light / Dark / System switch by only adding a toggle would create a fragile half-theme: some surfaces would switch, while editors, menus, file tree rows, dialogs, and embedded viewers would keep dark-only values. The right migration is to introduce a stable appearance layer first, then gradually move existing dark colors into semantic tokens and design a matching light palette.

The goal is not to replace the current dark design. The goal is to preserve it as the `dark` token set and add a first-class `light` token set beside it.

## 2. Product Requirements

PuppyOne should support three user-facing appearance modes:

- `System`: follow the OS/browser color scheme.
- `Light`: force PuppyOne light mode.
- `Dark`: force PuppyOne dark mode.

The selected mode should:

- Apply consistently across the main app shell, project pages, file tree, menus, dialogs, editors, access surfaces, history, and settings.
- Persist across reloads and browser tabs.
- Avoid a bright/dark flash on initial load.
- Keep existing dark-mode visual quality.
- Let complex embedded surfaces use specialized theme adapters instead of naive CSS inversion.

## 3. External Patterns

Mature products and design systems converge on the same structure:

- A stored user preference (`light`, `dark`, `system` / `auto`).
- A root DOM marker (`class="dark"`, `data-theme="dark"`, or `data-color-mode="auto"`).
- Semantic tokens for backgrounds, foregrounds, borders, controls, status, and accents.
- Component implementations that reference semantic tokens, not raw colors.
- Dedicated adapters for code editors, charts, PDF viewers, and embedded content.

Examples:

- Notion exposes `Use system setting`, `Light`, and `Dark` in Appearance settings.
- GitHub Primer uses `data-color-mode`, `data-light-theme`, `data-dark-theme`, and CSS variables such as foreground/background tokens.
- VS Code stores the active theme, can follow OS color scheme, and separately maps preferred light, dark, and high-contrast themes.
- shadcn/ui and Radix Themes commonly pair CSS variables/design tokens with `next-themes` for Next.js apps.

References:

- https://ui.shadcn.com/docs/dark-mode/next
- https://www.radix-ui.com/themes/docs/theme/dark-mode
- https://primer.style/product/primitives/
- https://code.visualstudio.com/docs/configure/themes
- https://www.notion.com/help/account-settings

## 4. Recommended PuppyOne Architecture

Use `next-themes` for color-mode state management and a PuppyOne-owned semantic token system for visual styling.

`next-themes` is responsible for:

- Reading/writing the user preference.
- Resolving `system` through `prefers-color-scheme`.
- Applying a root class or data attribute before paint.
- Syncing the preference across tabs.

PuppyOne tokens are responsible for:

- Defining the actual light and dark palettes.
- Giving components stable semantic names.
- Preserving PuppyOne's product taste.
- Supporting future variants without rewriting components.

The intended stack:

```tsx
<html suppressHydrationWarning>
  <body>
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      enableColorScheme
      disableTransitionOnChange
    >
      {children}
    </ThemeProvider>
  </body>
</html>
```

Root state:

```txt
theme preference: light | dark | system
resolved theme:   light | dark
DOM marker:       html.dark when resolved theme is dark
```

Component styling:

```tsx
style={{
  background: 'var(--po-canvas)',
  color: 'var(--po-text)',
  borderColor: 'var(--po-border)',
}}
```

or Tailwind semantic utilities:

```tsx
className="bg-po-canvas text-po-text border-po-border"
```

## 5. Token Layers

PuppyOne should use layered tokens rather than raw colors in components.

### 5.1 Primitive Tokens

Primitive tokens are raw palette values. They should mostly live in one global CSS file and rarely be referenced directly by components.

Examples:

```css
--po-gray-0
--po-gray-1
--po-gray-2
--po-blue-9
--po-red-9
```

### 5.2 Semantic Tokens

Semantic tokens describe UI purpose. These are the tokens components should use.

Core surface tokens:

```css
--po-canvas
--po-sidebar
--po-header
--po-panel
--po-panel-raised
--po-overlay
--po-inset
```

Text tokens:

```css
--po-text
--po-text-muted
--po-text-subtle
--po-text-disabled
--po-text-inverse
```

Border and divider tokens:

```css
--po-border
--po-border-subtle
--po-border-strong
--po-divider
```

Interactive tokens:

```css
--po-hover
--po-active
--po-selected
--po-focus-ring
--po-control
--po-control-hover
```

Brand and semantic state tokens:

```css
--po-accent
--po-accent-text
--po-success
--po-warning
--po-danger
--po-info
```

### 5.3 Component Tokens

Component tokens are allowed when a surface has repeated, specific needs. Keep these limited.

Examples:

```css
--po-sidebar-bg
--po-sidebar-row-hover
--po-sidebar-row-selected
--po-filetree-rail
--po-diff-added-bg
--po-diff-removed-bg
--po-editor-bg
```

Rule: prefer semantic tokens first. Create component tokens only when a component has a repeated visual grammar that would otherwise leak implementation details everywhere.

## 6. Initial Palette Direction

### 6.1 Dark Mode

The current dark design should be migrated almost as-is into the dark token set. This protects the existing visual quality and reduces migration risk.

Current dark anchors:

- App canvas: `#0e0e0e`
- Sidebar: `#121212`
- Header: `#0e0e0e`
- Subtle border: `rgba(255,255,255,0.08)`
- Primary text: near `#fafafa`
- Muted text: near `#a1a1aa`

### 6.2 Light Mode

The light palette should not be a simple inversion. PuppyOne is a dense workspace product, so the light mode should feel like Notion/Linear/GitHub: quiet, low-noise, functional, and easy to scan.

Initial direction:

- Canvas: slightly off-white, not pure white.
- Sidebar/header: white or near-white with restrained borders.
- Panels: white with subtle separation.
- Text: deep neutral, not pure black everywhere.
- Muted text: medium neutral with enough contrast.
- Selected rows: soft accent tint or neutral tint, not saturated blocks.
- Borders: low alpha neutral lines.

Example draft:

```css
:root {
  color-scheme: light;
  --po-canvas: #f6f7f8;
  --po-sidebar: #ffffff;
  --po-header: #ffffff;
  --po-panel: #ffffff;
  --po-panel-raised: #ffffff;
  --po-overlay: #ffffff;
  --po-inset: #f1f3f5;

  --po-text: #18181b;
  --po-text-muted: #626a73;
  --po-text-subtle: #8a929d;
  --po-text-disabled: #b6bec8;
  --po-text-inverse: #ffffff;

  --po-border: rgba(15, 23, 42, 0.12);
  --po-border-subtle: rgba(15, 23, 42, 0.08);
  --po-border-strong: rgba(15, 23, 42, 0.18);
  --po-divider: rgba(15, 23, 42, 0.10);

  --po-hover: rgba(15, 23, 42, 0.045);
  --po-active: rgba(15, 23, 42, 0.07);
  --po-selected: rgba(37, 99, 235, 0.10);
  --po-focus-ring: rgba(37, 99, 235, 0.35);

  --po-accent: #2563eb;
  --po-accent-text: #1d4ed8;
  --po-success: #15803d;
  --po-warning: #b45309;
  --po-danger: #dc2626;
  --po-info: #0284c7;
}

.dark {
  color-scheme: dark;
  --po-canvas: #0e0e0e;
  --po-sidebar: #121212;
  --po-header: #0e0e0e;
  --po-panel: #161616;
  --po-panel-raised: #1a1a1a;
  --po-overlay: #1f1f23;
  --po-inset: #0a0a0a;

  --po-text: #fafafa;
  --po-text-muted: #a1a1aa;
  --po-text-subtle: #71717a;
  --po-text-disabled: #52525b;
  --po-text-inverse: #0a0a0a;

  --po-border: rgba(255, 255, 255, 0.08);
  --po-border-subtle: rgba(255, 255, 255, 0.06);
  --po-border-strong: rgba(255, 255, 255, 0.14);
  --po-divider: rgba(255, 255, 255, 0.08);

  --po-hover: rgba(255, 255, 255, 0.04);
  --po-active: rgba(255, 255, 255, 0.07);
  --po-selected: rgba(255, 255, 255, 0.08);
  --po-focus-ring: rgba(96, 165, 250, 0.45);

  --po-accent: #60a5fa;
  --po-accent-text: #93c5fd;
  --po-success: #34d399;
  --po-warning: #fbbf24;
  --po-danger: #f87171;
  --po-info: #67e8f9;
}
```

This is only a starting point. The light palette should be tuned visually after the first shell migration.

## 7. State and Persistence

### 7.1 V1

Use `next-themes` local storage persistence.

Pros:

- Fast to ship.
- No backend dependency.
- Works before auth state loads.
- Avoids first-paint flash.
- Syncs between tabs.

Cons:

- Preference is browser-local, not account-level.

### 7.2 Later Account Sync

If we want Notion-style account-wide appearance:

- Add `appearance_mode` to user preferences: `system | light | dark`.
- On authenticated app load, reconcile local preference and account preference.
- Save changes back to the account.
- Keep local storage as a pre-auth/first-paint cache.

Do not block V1 on account sync. The UI architecture should allow it later.

## 8. Tailwind Strategy

Current Tailwind config has no dark-mode selector or semantic color extension. Add:

```js
module.exports = {
  darkMode: 'selector',
  theme: {
    extend: {
      colors: {
        po: {
          canvas: 'var(--po-canvas)',
          sidebar: 'var(--po-sidebar)',
          header: 'var(--po-header)',
          panel: 'var(--po-panel)',
          overlay: 'var(--po-overlay)',
          text: 'var(--po-text)',
          muted: 'var(--po-text-muted)',
          subtle: 'var(--po-text-subtle)',
          border: 'var(--po-border)',
          accent: 'var(--po-accent)',
        },
      },
    },
  },
};
```

Then prefer semantic classes:

```tsx
className="bg-po-canvas text-po-text border-po-border"
```

Avoid adding many `dark:*` utility pairs in product components. They are fine for one-off styling, but PuppyOne needs maintainable app-wide theming.

## 9. Theme-Aware Runtime APIs

Some code cannot be fully solved by CSS variables. Add a small frontend theme utility layer.

Suggested files:

```txt
frontend/components/theme/ThemeProvider.tsx
frontend/components/theme/ThemeToggle.tsx
frontend/lib/theme/tokens.css or app/theme.css
frontend/lib/theme/useResolvedTheme.ts
frontend/lib/theme/editorThemes.ts
```

Responsibilities:

- `ThemeProvider`: wraps `next-themes`.
- `ThemeToggle`: Light / Dark / System UI, mounted safely to avoid hydration mismatch.
- `useResolvedTheme`: returns `light | dark`, with a safe fallback.
- `editorThemes.ts`: registers and switches Monaco themes.

## 10. Migration Inventory

### 10.1 Global App Shell

Files:

- `frontend/app/layout.tsx`
- `frontend/app/globals.css`
- `frontend/app/(main)/layout.tsx`
- `frontend/components/sidebar/SidebarLayout.tsx`
- `frontend/components/sidebar/ProjectSwitcher.tsx`
- `frontend/components/ProjectsHeader.tsx`

Work:

- Add theme provider.
- Replace shell colors with CSS variables.
- Theme scrollbars.
- Ensure menus and overlays inherit tokenized surfaces.

### 10.2 Navigation and Workspace Surfaces

Files:

- `frontend/components/AppSidebar.tsx`
- `frontend/components/ContextSidebar.tsx`
- settings and tools sidebars
- project/org switcher components

Work:

- Tokenize hover, selected, border, avatar, muted labels.
- Keep brand blue consistent but tune contrast in light mode.
- Verify collapsed and expanded sidebars in both modes.

### 10.3 Data Explorer

Files:

- `frontend/app/(main)/projects/[projectId]/data/[[...path]]/page.tsx`
- `frontend/app/(main)/projects/[projectId]/data/components/explorer/*`
- `frontend/app/(main)/projects/[projectId]/data/components/menus/*`
- `frontend/app/(main)/projects/[projectId]/data/components/FileViewerHeaderActions.tsx`

Work:

- Tokenize file tree backgrounds, rails, row hover, row selection, and contextual menus.
- Re-check file icon colors in light mode.
- Avoid color overload: light mode should use shape and hierarchy, not excessive saturated type colors.

### 10.4 Editors and Viewers

Files:

- `frontend/components/editors/code/*`
- `frontend/components/editors/markdown/*`
- `frontend/components/editors/table/*`
- `frontend/components/editors/vanilla/VanillaJsonEditor.tsx`
- `frontend/styles/jsoneditor-custom.css`
- `frontend/components/editors/html/HtmlArtifactPreview.tsx`
- `frontend/components/editors/pdf/PdfPreview.tsx`
- `frontend/components/editors/image/*`
- `frontend/components/editors/audio/*`
- `frontend/components/editors/video/*`

Work:

- Monaco: register `code-light`, `code-dark`, `markdown-light`, `markdown-dark`, `json-light`, `json-dark`; switch with resolved theme.
- Milkdown: move dark CSS values to CSS variables or generate light/dark CSS blocks.
- JSONEditor: replace dark-only `!important` values with tokenized values; this is one of the highest-risk areas.
- HTML artifact iframe: do not forcibly theme the user's HTML document. Only theme the surrounding PuppyOne chrome.
- PDF viewer: browser/PDF.js toolbar theming is limited. V1 should focus on surrounding chrome.
- Media previews: canvas/background/checkerboard should be tokenized.

### 10.5 History and Diff

Files:

- `frontend/app/(main)/projects/[projectId]/history/page.tsx`

Work:

- Tokenize timeline rail, selected commit, diff added/removed rows, hunk headers, file cards.
- Light mode diff colors need special care: green/red backgrounds should be muted enough for dense reading.

### 10.6 Access Points and Connectors

Files:

- `frontend/app/(main)/projects/[projectId]/access/*`
- `frontend/app/(main)/projects/[projectId]/data/components/access-points/*`
- `frontend/app/(main)/projects/[projectId]/data/components/github-integration/*`

Work:

- Consolidate existing local token files into global theme tokens where possible.
- Keep status colors semantic and accessible in both modes.
- Verify command blocks and prompt blocks in light mode.

### 10.7 Dialogs, Menus, Toasts, Loading

Files:

- `frontend/components/ItemActionMenu.tsx`
- `frontend/components/FolderManageDialog.tsx`
- `frontend/components/NodeRenameDialog.tsx`
- `frontend/components/loading/*`
- import/connect dialogs
- onboarding components

Work:

- Tokenize overlays, scrims, modal panels, inputs, primary/secondary buttons, loaders.
- Ensure portal menus stay above iframes/PDFs in both modes.

### 10.8 Auth and Public Pages

Files:

- `frontend/app/login/page.tsx`
- `frontend/app/reset-password/page.tsx`
- `frontend/app/not-found.tsx`
- OAuth callback pages

Work:

- Decide whether auth pages follow app theme in V1.
- If yes, tokenize them after main app shell.
- If no, document them as dark-only until V2.

## 11. Migration Phases

### Phase 0: Audit and Token Contract

Deliverables:

- This architecture document.
- Token list approved by product/design.
- No behavior changes.

Risk: low.

### Phase 1: Theme Foundation

Deliverables:

- Install and wire `next-themes`.
- Add root `ThemeProvider`.
- Add CSS variables for light/dark.
- Add Tailwind semantic color mapping.
- Add mounted-safe `ThemeToggle`.
- Place toggle in a low-noise location, likely user menu or settings.

Risk: low to medium.

### Phase 2: App Shell Migration

Deliverables:

- Main layout, sidebar, project switcher, top header, global menus, overlays, scrollbars.
- Product shell looks coherent in light and dark.

Risk: medium.

This is the first visual checkpoint. Do not continue broad migration until shell quality is approved.

### Phase 3: Core Workspace Migration

Deliverables:

- Data explorer.
- File tree.
- Header actions.
- Access panel.
- History page.
- Major dialogs.

Risk: medium to high.

### Phase 4: Editor and Viewer Migration

Deliverables:

- Monaco light/dark themes.
- Milkdown light/dark themes.
- JSONEditor tokenized CSS.
- Media/PDF/HTML surrounding chrome.

Risk: high because editors have their own theming mechanisms and some CSS uses `!important`.

### Phase 5: Remaining Pages and Polish

Deliverables:

- Auth pages.
- Onboarding.
- Agent runtime/config pages.
- Tools/server pages.
- Visual QA across all major routes.

Risk: medium.

## 12. Rules for Code Migration

1. Do not introduce new raw neutral colors in components.
2. Keep brand/status colors centralized.
3. Prefer semantic tokens over `dark:*` class pairs.
4. Avoid naive inversion.
5. Do not theme user-authored HTML artifacts inside the iframe.
6. Do not remount editors just to switch theme if the editor supports runtime theme changes.
7. Do not make the theme toggle visually loud. It belongs in settings/user menu, not the main workspace command surface.
8. New components must support both modes from the start.

## 13. Testing and Acceptance

### Functional Checks

- Initial load follows system preference when no saved preference exists.
- User can select Light, Dark, and System.
- Preference survives reload.
- Preference syncs across tabs.
- No hydration warning from the toggle UI.
- No visible first-paint flash in production build.

### Visual Checks

Test the following in both modes:

- Home
- Project Data page
- File tree with folders, Markdown, JSON, PDF, HTML, images, audio
- Markdown live/source modes
- JSON table/source modes
- HTML preview/source modes
- PDF preview
- History page and diff hunks
- Access page
- Access Point detail panel
- Settings
- User/project switcher menus
- Dialogs and destructive flows

### Accessibility Checks

- Text contrast should meet WCAG AA for normal text.
- Focus rings must remain visible in both modes.
- Error/success/warning colors must not rely only on hue.
- `color-scheme` should be set so native inputs and scrollbars behave correctly.

## 14. Known Risks

- The codebase currently has many inline dark colors. Partial migration will create inconsistent surfaces.
- JSONEditor CSS has many dark-only overrides and `!important`; it will need careful treatment.
- Monaco/Milkdown theme switching must be integrated with resolved theme state.
- Light mode may reveal spacing/border issues hidden by dark mode.
- User-generated HTML content should remain visually independent, which may surprise users expecting all content to change.
- Account-level preference sync is a separate product decision.

## 15. Recommended First Implementation Slice

The first implementation should be intentionally narrow:

1. Add `next-themes` and `ThemeProvider`.
2. Add PuppyOne CSS variables for both modes.
3. Add Tailwind semantic color aliases.
4. Add theme toggle in the user menu or settings.
5. Migrate only:
   - main app layout,
   - sidebar,
   - project switcher,
   - top project header,
   - global popover/menu surfaces,
   - scrollbars.
6. Run visual QA on shell pages.

This gives PuppyOne a real theme foundation without touching every editor and page at once. After the shell passes review, migrate the core workspace and editors in separate follow-up slices.
