'use client';

import { useContext, useEffect, useLayoutEffect, useState, useSyncExternalStore } from 'react';
import { EditorSessionContext } from './editor/EditorSessionProvider';
import { createEditorSessionRegistry } from './editor/session';

export type SaveStatus = 'clean' | 'dirty' | 'saving' | 'saved' | 'error';


export interface UseManualSaveOptions<T> {
  /** Stable per-file key — e.g. `"projectId:nodeId"`. Used for the
   *  localStorage draft slot and to detect file switches. */
  readonly fileKey: string;
  /** The last value known to be on the server. Drives the initial
   *  `clean` state and the equality check that determines `dirty`. */
  readonly serverContent: T;
  /** Predicate for "is this draft equal to the server value?"
   *  Defaults to reference equality, which is correct for primitives
   *  but wrong for objects — pass a deep-equal for tables/JSON. */
  readonly isEqual?: (a: T, b: T) => boolean;
  /** T → string for localStorage. Defaults to JSON.stringify, which
   *  works for both string content and JSON objects. Override only
   *  if you have a more compact serialization. */
  readonly serialize?: (value: T) => string;
  /** string → T for localStorage. Inverse of `serialize`. */
  readonly deserialize?: (raw: string) => T;
  /** The actual save action — fires the server PATCH/POST. The hook
   *  flips state to 'saving' before this runs and to 'saved' on
   *  resolve / 'error' on reject. */
  readonly save: (value: T) => Promise<void>;
  /** Optional: skip draft restoration even if a localStorage entry
   *  exists. Used when the parent has already merged drafts itself
   *  (rare). */
  readonly skipDraftRestore?: boolean;
}

export interface UseManualSaveResult<T> {
  /** The current local value — what the editor binds against. */
  draft: T;
  /** Mutate the draft. Marks dirty and writes to localStorage. */
  setDraft: (next: T) => void;
  /** Coarse status enum the UI renders against. */
  status: SaveStatus;
  /** Convenience: `status === 'dirty' || status === 'error'`. */
  dirty: boolean;
  /** Last save error. Cleared on edit, discard, or a new save attempt. */
  error: string | null;
  /** Trigger a save. No-op if not dirty (avoids accidental double-
   *  commit on Cmd+S spam). */
  save: () => Promise<void>;
  /** Throw away the draft and revert to `serverContent`. */
  discard: () => void;
  /** True iff the current `draft` was hydrated from localStorage on
   *  mount (rather than from `serverContent`). The UI can read this
   *  to show a "draft restored" banner. */
  hasRestoredDraft: boolean;
  /** Dismiss the restored-draft banner without changing state.
   *  Doesn't clear the draft — the user is implicitly accepting
   *  it as their working copy. */
  acknowledgeRestoredDraft: () => void;
  /** When the local draft was written (via setDraft) or restored
   *  from disk. `null` while clean. Drives "Last edited Xs ago"
   *  copy. */
  lastEditedAt: number | null;
  /** When the last successful save landed. `null` until the first
   *  successful save. Drives "Last saved Xs ago" copy. */
  lastSavedAt: number | null;
}

/** Subscribe to a file-owned session. Unmounting an editor only detaches its
 * subscription; writes finish against their captured file and draft snapshot. */
export function useManualSave<T>(options: UseManualSaveOptions<T>): UseManualSaveResult<T> {
  const shared = useContext(EditorSessionContext);
  const [local] = useState(() => createEditorSessionRegistry());
  const registry = shared ?? local;
  const session = registry.get(options);
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);
  useLayoutEffect(() => { session.configure(options); registry.prune(); });
  useEffect(() => {
    const flush = () => session.flush();
    const visibility = () => { if (document.visibilityState === 'hidden') flush(); };
    window.addEventListener('pagehide', flush);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      flush();
      window.removeEventListener('pagehide', flush);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [session]);
  return { ...snapshot, draft: session.preview(options), ...session.actions, dirty: snapshot.status === 'dirty' || snapshot.status === 'error' };
}
