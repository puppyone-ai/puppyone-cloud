# PuppyOne Shared UI

This directory is the source of truth for reusable PuppyOne product UI.

The standalone Desktop app keeps its own vendored copy outside this repository.
When shared components change in a way Desktop needs, update that app's vendored
copy from this source.

## Boundaries

Shared UI may depend on React, browser-safe DOM APIs, and local files in this
directory. It must not depend on Next.js routing, Supabase, SWR, Electron,
Tauri, Node filesystem APIs, or app-specific source trees.

Platform-specific shell code stays in `frontend/` or the standalone Desktop app.
