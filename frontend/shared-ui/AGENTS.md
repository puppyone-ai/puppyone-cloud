# Shared UI Instructions

- This is the editable source of truth for Cloud/Desktop shared product UI.
- Do not import from `frontend/`, standalone desktop app source trees, `@/`, `next/*`, `electron`,
  `@tauri-apps/*`, `@supabase/*`, or `swr`.
- Prefer product-semantic component names such as `ExplorerTree`,
  `EditorHost`, and `FilePreview`.
- Keep app shell concerns out of this package. Cloud auth/routing and desktop
  native window behavior belong in their app directories.
- When a shared UI change affects Desktop, update the standalone Desktop app's
  vendored copy from this source.
