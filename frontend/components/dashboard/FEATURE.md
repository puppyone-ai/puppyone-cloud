# Cloud Project Launcher Feature Contract

This file records product and engineering agreements for the cloud project
launcher and project creation flow. When project creation or project-list behavior
changes, update this contract in the same patch.

## Project List UX

- The cloud home mirrors the Desktop project launcher: a compact, single-column
  list instead of folder-shaped project cards.
- Sort projects by `updated_at` descending without mutating the project array
  owned by the data hook. Projects without a usable timestamp sort last.
- Each row is a native button with an accessible project-specific label and a
  relative last-updated value.
- Keep the list scrollable inside the centered launcher so large project sets do
  not move the create action off-screen.
- The create action remains available when the list is empty and uses the same
  local pending state as the populated launcher.
- An organization or project-list request failure must render a retryable error
  state. Never collapse a failed initial request into the empty-project state.

## Untitled Project Names

- Default project names are resolved from occupied name slots, not from the
  current project count.
- Treat `Untitled Project` as slot `1`; treat `Untitled Project 2` and
  `Untitled Project (2)` as slot `2`, and so on.
- Creating a default-named project uses the first available slot among existing
  Untitled-style project names.
- Example: if slots `1`, `2`, `4`, and `5` exist, the next default name is
  `Untitled Project 3`.
- If a deleted project leaves a gap, that slot may be reused. Avoiding reuse of
  deleted slots would require a separate durable org-level sequence.
- Custom project names are not rewritten by the Untitled naming helper.
- The frontend may propose the next available name for responsiveness, but the
  backend must also resolve Untitled-style name collisions before create so
  stale caches or parallel windows do not produce duplicate default names.

## Project Creation UX

- Clicking create should put the create button into a local creating state.
- Do not insert a fake project row before the backend returns.
- After the backend create succeeds, navigate directly to the new project's data
  page.
- If create fails, clear the local creating state and show the failure without
  leaving a stale pending project in the dashboard.
