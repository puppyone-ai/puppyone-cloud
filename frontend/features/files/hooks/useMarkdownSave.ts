'use client';

import { writeFile, type NodeType } from '@/lib/contentTreeApi';
import {
  useManualSave,
  type SaveStatus,
} from '@/lib/hooks/useManualSave';
import { useCallback, useMemo } from 'react';

/**
 * useMarkdownSave — manual-save state for markdown editor sessions.
 *
 * Replaces the older `useMarkdownAutoSave` (1.5s debounced PATCH per
 * keystroke). The new contract:
 *
 *   - User edits → local draft updates, NO server call.
 *   - User explicitly hits Cmd+S / clicks Save → exactly one
 *     `writeFile` round-trip → exactly one version commit.
 *   - localStorage drafts survive tab close / refresh.
 *
 * The hook is just a markdown-flavoured shell over the generic
 * `useManualSave`. The shell:
 *
 *   1. Pins `serialize` / `deserialize` / `isEqual` to string-
 *      identity (markdown content is plain text).
 *   2. Builds the `fileKey` from `(projectId, activeNodePath)` so
 *      every file gets its own isolated draft slot.
 *   3. Wraps the `writeFile` API call so the consumer doesn't see
 *      the persistence detail.
 *   4. Re-exports the most-used fields under markdown-specific
 *      names (`markdownContent`, `handleMarkdownChange`,
 *      `markdownSaveStatus`) so the page-level call site reads
 *      naturally without leaking the generic-T abstraction.
 *
 * Why a shell instead of using `useManualSave` directly: the call
 * site in `page.tsx` already has a markdown-shaped vocabulary
 * baked into prop names (`markdownContent`, `handleMarkdownChange`,
 * `markdownSaveStatus` flow through to `EditorArea` and the
 * editor chrome). Renaming everything to the generic-T vocabulary
 * would touch a dozen files for no UX win. The shell hides the
 * generic and keeps the existing call-site shape.
 */

export interface UseMarkdownSaveOptions {
  readonly projectId: string;
  /** Path to the file relative to the project root. Doubles as the
   *  argument to `writeFile`. Empty string when no file is active —
   *  the hook degrades to a no-op save in that case. */
  readonly activeNodePath: string;
  /** Server-side content for the active file. Initialised by the
   *  path resolver after the file loads; used as the dirty-check
   *  baseline. */
  readonly serverContent: string;
  readonly nodeType?: Extract<NodeType, 'markdown' | 'file'>;
}

export interface UseMarkdownSaveResult {
  /** Current draft text — what the editor binds to. May differ from
   *  `serverContent` when the user has unsaved edits. */
  readonly markdownContent: string;
  /** Editor onChange wire-up. Updates the draft, marks dirty, and
   *  writes to localStorage. Does NOT call the server. */
  readonly handleMarkdownChange: (next: string) => void;
  /** Five-state status enum the EditorSaveButton renders against. */
  readonly markdownSaveStatus: SaveStatus;
  /** True iff there are unsaved edits (status === 'dirty' || 'error').
   *  Drives navigation guards (beforeunload, file-switch confirm). */
  readonly dirty: boolean;
  /** Trigger a save — wired to Cmd+S + the EditorSaveButton click. */
  readonly save: () => Promise<void>;
  /** Discard the draft, revert to `serverContent`. */
  readonly discard: () => void;
  /** True on the first render after a localStorage draft was hydrated
   *  on file open — the page renders a "draft restored" banner
   *  while this is true. */
  readonly hasRestoredDraft: boolean;
  /** Dismiss the restored-draft banner. */
  readonly acknowledgeRestoredDraft: () => void;
}

export function useMarkdownSave({
  projectId,
  activeNodePath,
  serverContent,
  nodeType = 'markdown',
}: UseMarkdownSaveOptions): UseMarkdownSaveResult {
  // The fileKey scopes every per-file artefact (the localStorage
  // draft slot, the dirty flag, the file-change effect). We
  // include `projectId` so two different projects with a same-named
  // file don't share drafts. Empty `activeNodePath` produces a
  // distinct "no-file" key so the hook is harmless when no file is
  // open.
  const fileKey = useMemo(
    () => `${nodeType}:${projectId}:${activeNodePath || '(none)'}`,
    [nodeType, projectId, activeNodePath],
  );

  // Markdown content is a plain string — string-identity is the
  // right equality check. `serialize` / `deserialize` are no-ops
  // because we already store strings; the JSON envelope is added
  // by the inner hook.
  const stringIdentity = useCallback((s: string) => s, []);
  const stringEquals = useCallback((a: string, b: string) => a === b, []);

  // The save action wires the snapshot from the hook into the
  // existing `writeFile` API. Empty path means "no file open" —
  // we throw rather than silently swallow because the caller
  // shouldn't be trying to save in that state.
  const save = useCallback(
    async (snapshot: string) => {
      if (!activeNodePath) {
        throw new Error('Cannot save: no active text file');
      }
      await writeFile(projectId, activeNodePath, snapshot, nodeType);
    },
    [projectId, activeNodePath, nodeType],
  );

  const inner = useManualSave<string>({
    fileKey,
    serverContent,
    isEqual: stringEquals,
    serialize: stringIdentity,
    deserialize: stringIdentity,
    save,
    // When no file is open, we still want a clean draft (no stale
    // restore banner). The dedicated `(none)` fileKey makes drafts
    // for "no file" slots impossible to save anyway, but skipping
    // the restore prevents a confusing "draft from a stale entry"
    // banner if anything ever wrote one.
    skipDraftRestore: !activeNodePath,
  });

  return {
    markdownContent: inner.draft,
    handleMarkdownChange: inner.setDraft,
    markdownSaveStatus: inner.status,
    dirty: inner.dirty,
    save: inner.save,
    discard: inner.discard,
    hasRestoredDraft: inner.hasRestoredDraft,
    acknowledgeRestoredDraft: inner.acknowledgeRestoredDraft,
  };
}
