'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createDraftWriter } from './draftPersistence';

/**
 * useManualSave — generic dirty-tracking + manual-save state machine
 * for any editor whose content the user can mutate locally before
 * choosing to commit it to the server.
 *
 * ## Why this exists
 *
 * The previous auto-save model (debounced PATCH on every change)
 * was producing 100+ commits per editing session — every 1.5s of
 * typing was its own commit in the version history. That broke
 * three things:
 *
 *   1. Version history became un-navigable (100 noise entries per
 *      "I edited this paragraph" intent).
 *   2. Diffs lost meaning (commit N → N+1 was always "added one
 *      character").
 *   3. Backend cost amplified (each commit triggers version engine
 *      hashing, S3 dedup, audit log, search-index refresh).
 *
 * Switching to manual save (Cmd+S = one commit) collapses each
 * editing session into one logical checkpoint — what users mean
 * when they "save".
 *
 * ## State machine
 *
 *           ┌─────────────┐
 *           │    clean    │ ← initial state, also after a successful save
 *           └──────┬──────┘
 *                  │ user edits
 *                  ▼
 *           ┌─────────────┐
 *      ┌──→ │    dirty    │ ←──┐ user keeps editing
 *      │    └──────┬──────┘    │
 *      │           │ save()    │ user discards
 *      │           ▼           │
 *      │    ┌─────────────┐    │
 *      │    │   saving    │    │
 *      │    └──────┬──────┘    │
 *      │     OK    │    error  │
 *      │           ▼           │
 *      │    ┌─────────────┐    │
 *      │    │    saved    │    │ ────────┐
 *      │    └──────┬──────┘    │         │
 *      │     2s    │           │         │ user edits
 *      └───────────┘           │         │ during the
 *                              │         │ "saved" flash
 *                              │         ▼
 *                              │  ┌─────────────┐
 *                              └──┤    error    │
 *                                 └─────────────┘
 *
 * `saved` is a transient state — it auto-fades back to `clean`
 * after `STATUS_FLASH_MS`. `error` does not auto-fade; the user
 * has to retry or discard.
 *
 * ## Local-storage draft persistence
 *
 * Edits are coalesced for at most 250 ms, then persisted under the
 * key `puppyone:editor-draft:{fileKey}`. This lets us survive:
 *
 *   - Browser tab close mid-edit (next reopen restores the draft).
 *   - Hard refresh.
 *   - Crash / power loss (up to the 250 ms window and browser buffering).
 * Navigation/unmount, pagehide and backgrounding flush pending drafts.
 *
 * On mount, we check localStorage. If a draft exists *and* differs
 * from `serverContent`, we hydrate `draft` from the draft and emit
 * `hasRestoredDraft = true` so the UI can show a "draft restored"
 * banner. If the draft equals serverContent (e.g. user saved in
 * another tab), we discard it silently.
 *
 * On successful save we clear the draft entry. On `discard()` we
 * also clear it.
 *
 * ## When `serverContent` changes
 *
 * Two cases trigger a `serverContent` change:
 *
 *   1. The user switches to a different file (the parent passes a
 *      new `serverContent` for the new file's payload). `fileKey`
 *      also changes, so we treat this as a fresh mount and re-run
 *      the draft-restore logic.
 *
 *   2. We just successfully saved (the parent re-fetches and the
 *      new server value comes back). `fileKey` stays the same. We
 *      pull the new server value into `draft` *if and only if* the
 *      user hasn't typed anything since the save (status was
 *      `saved` or `clean`); otherwise we leave their local draft alone.
 */
export type SaveStatus = 'clean' | 'dirty' | 'saving' | 'saved' | 'error';

const DRAFT_KEY_PREFIX = 'puppyone:editor-draft:';
/** How long to flash 'saved' before falling back to 'clean'. Long
 *  enough to register, short enough to feel instant. */
const STATUS_FLASH_MS = 1500;

/** Envelope written to localStorage. The timestamp gives us the
 *  "draft from 3 min ago" copy on the restore banner without making
 *  the consumer do its own bookkeeping. */
interface DraftEnvelope {
  savedAt: number;
  payload: string;
}

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

/** Collapse trailing whitespace on each line and strip leading/trailing
 *  blank lines, so two serialized values that differ only by server-side
 *  whitespace normalization (e.g. a trimmed trailing newline) compare equal. */
const normalizeWhitespace = (s: string): string =>
  s.replace(/[ \t]+$/gm, '').replace(/\n+$/g, '').replace(/^\n+/g, '');

const defaultIsEqual = <T>(a: T, b: T) => Object.is(a, b);
const defaultSerialize = <T>(v: T) => JSON.stringify(v);
const defaultDeserialize = <T>(raw: string) => JSON.parse(raw) as T;

export function useManualSave<T>({
  fileKey,
  serverContent,
  isEqual = defaultIsEqual,
  serialize = defaultSerialize,
  deserialize = defaultDeserialize,
  save: saveFn,
  skipDraftRestore = false,
}: UseManualSaveOptions<T>): UseManualSaveResult<T> {
  // ── Local state ────────────────────────────────────────────────
  //
  // `draft` is what the editor reads/writes. It diverges from
  // `serverContent` while the user types and converges back on
  // save / discard / clean reload.
  const [draft, setDraftState] = useState<T>(serverContent);
  const [status, setStatus] = useState<SaveStatus>('clean');
  const [error, setError] = useState<string | null>(null);
  const [hasRestoredDraft, setHasRestoredDraft] = useState(false);
  const [lastEditedAt, setLastEditedAt] = useState<number | null>(null);
  const [lastSavedAt, setLastSavedAt] = useState<number | null>(null);

  // Refs hold the *latest* draft + status for async callbacks
  // (save) and for the beforeunload listener that needs to read
  // them outside of React's render cycle.
  const draftRef = useRef(draft);
  const statusRef = useRef(status);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  draftRef.current = draft;
  statusRef.current = status;

  // The flash timer that demotes `saved` → `clean`. We stash it on
  // a ref so a fresh save can cancel a pending demote (otherwise
  // back-to-back saves would race the demote and end up in a
  // visually-confusing transient state).
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Cleanup timer on unmount.
  useEffect(() => {
    return () => {
      if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
    };
  }, [fileKey]);

  // ── localStorage helpers ──────────────────────────────────────
  //
  // Wrapped to swallow any error from quota / private mode / SSR
  // (`window` is undefined). The hook degrades to in-memory drafts
  // if localStorage is unavailable — saves still work, only the
  // "draft survives reload" benefit is lost.
  const draftKey = useMemo(() => `${DRAFT_KEY_PREFIX}${fileKey}`, [fileKey]);

  const writeDraft = useCallback(
    (value: T) => {
      if (typeof window === 'undefined') return;
      try {
        const env: DraftEnvelope = {
          savedAt: Date.now(),
          payload: serialize(value),
        };
        window.localStorage.setItem(draftKey, JSON.stringify(env));
      } catch {
        // Swallow — quota exceeded, private mode, etc. The in-
        // memory draft is still authoritative; we just lose
        // cross-reload persistence for this entry.
      }
    },
    [draftKey, serialize],
  );

  const writer = useMemo(() => createDraftWriter(writeDraft), [writeDraft]);
  useEffect(() => {
    const onVisibility = () => { if (document.visibilityState === 'hidden') writer.flush(); };
    window.addEventListener('pagehide', writer.flush);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      writer.flush();
      window.removeEventListener('pagehide', writer.flush);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [writer]);

  const clearDraft = useCallback(() => {
    writer.cancel();
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.removeItem(draftKey);
    } catch {
      // Same swallow rationale as writeDraft.
    }
  }, [draftKey, writer]);

  const readDraft = useCallback((): T | null => {
    if (typeof window === 'undefined') return null;
    try {
      const raw = window.localStorage.getItem(draftKey);
      if (!raw) return null;
      const env = JSON.parse(raw) as DraftEnvelope;
      if (typeof env.payload !== 'string') return null;
      return deserialize(env.payload);
    } catch {
      return null;
    }
  }, [draftKey, deserialize]);

  // ── Render-body state sync ────────────────────────────────────
  //
  // Earlier versions of this hook handled `fileKey` and
  // `serverContent` changes in `useEffect`. That broke editors
  // whose internal state is initialised exactly once on mount —
  // for example rich/code editor wrappers that read their initial
  // document at construction time and ignore subsequent prop
  // changes unless the parent remounts or dispatches an explicit
  // external update.
  //
  // The failure mode looked like this:
  //
  //   Render N   : fileKey changes (user opened a markdown file).
  //                serverContent is still '' (async fetch in flight).
  //                file-change effect runs *after* commit:
  //                setDraftState(''). draft = ''.
  //   Render N+1 : serverContent loads to '# Hello'.
  //                isLoadingText flips to false.
  //                <MarkdownEditor> *mounts* with content=draft=''.
  //                The embedded editor initialises its internal
  //                state from that empty string.
  //                After commit, serverContent-change effect runs:
  //                setDraftState('# Hello'). draft updates.
  //   Render N+2 : draft = '# Hello' arrives at <MarkdownEditor>,
  //                but the embedded editor ignores prop changes —
  //                it's already mounted with empty content, and
  //                stays that way.
  //
  // Result: every markdown file opened with manual-save enabled
  // looked empty, even though the API returned content correctly.
  //
  // The fix is to do the sync *during render*, not after. React
  // explicitly supports this pattern for "deriving state from
  // props" — see
  // https://react.dev/reference/react/useState#storing-information-from-previous-renders
  //
  // When `setState` is called during render, React discards the
  // current render's output, immediately re-renders with the new
  // state, and only commits the final result. So child components
  // embedded editors read the *synced* draft on first mount and
  // initialise from real content rather than the stale ''.
  //
  // Refs hold the previous fileKey / serverContent so we can detect
  // changes. Refs are mutable across renders without triggering
  // re-renders, which is exactly what we need here.
  const prevFileKeyRef = useRef(fileKey);
  const fileGeneration = useRef(0);
  const prevServerContentRef = useRef(serverContent);
  // localStorage is impure I/O and MUST NOT run in the render body — a
  // discarded / double render (React strict mode, a bailed-out sync render)
  // would otherwise `clearDraft()` and wipe a still-valid persisted draft.
  // Instead the render body only decides *what* to do and records it here;
  // an effect below performs the actual read/clear after commit.
  const pendingClearRef = useRef(false);
  // Set to a fileKey when render needs the persisted draft for that key read
  // back in on the next commit. Reads are deferred out of render too so the
  // render stays pure.
  const pendingRestoreKeyRef = useRef<string | null>(skipDraftRestore ? null : fileKey);

  if (prevFileKeyRef.current !== fileKey) {
    fileGeneration.current += 1;
    // Case 1: file switched. Re-init draft + status. The persisted draft
    // (if any) is restored asynchronously in the effect below; here we
    // optimistically start from serverContent so Milkdown (which reads
    // `defaultValue` only at mount) initialises from real content.
    setDraftState(serverContent);
    setStatus('clean');
    setError(null);
    setHasRestoredDraft(false);
    setLastEditedAt(null);
    // Defer the localStorage read/reconcile to the post-commit effect.
    pendingRestoreKeyRef.current = skipDraftRestore ? null : fileKey;
    prevFileKeyRef.current = fileKey;
    prevServerContentRef.current = serverContent;
  } else if (!isEqual(prevServerContentRef.current, serverContent)) {
    // Case 2: same file, but serverContent updated (e.g. async fetch
    // completed, or another tab saved). Two sub-cases:
    //
    //   A) We're idle (status 'clean' or 'saved') — pull the new
    //      server value into the draft. Keeps the editor in lockstep
    //      with whatever's authoritative.
    //
    //   B) We're dirty / error and the new server value happens to
    //      equal the draft (e.g. file-change init landed before
    //      async fetch completed, or another tab saved an identical
    //      value) — demote to 'clean' and clear the localStorage
    //      entry. Otherwise the user is stuck with a phantom dirty
    //      state.
    //
    // The 'saving' state is left alone in either case —
    // interrupting an in-flight save with a state change would
    // scramble the eventual save handler.
    if (statusRef.current !== 'saving') {
      if (statusRef.current === 'clean' || statusRef.current === 'saved') {
        // Bug guard: after a save flashes 'saved', the parent often re-fetches
        // and hands back server-*normalized* content (e.g. a trimmed trailing
        // newline). Silently replacing the on-screen text with that — with no
        // dirty indicator — is jarring and looks like data loss. If the only
        // difference from what's already on screen is whitespace normalization,
        // leave the editor's text alone. Genuine server changes (real content
        // diffs, or a file the user isn't actively editing) still sync.
        const isSavedWindow = statusRef.current === 'saved';
        const onlyWhitespaceDiff =
          isSavedWindow &&
          normalizeWhitespace(serialize(draftRef.current)) ===
            normalizeWhitespace(serialize(serverContent));
        if (!onlyWhitespaceDiff) {
          setDraftState(serverContent);
        }
      } else if (isEqual(draftRef.current, serverContent)) {
        pendingClearRef.current = true;
        setStatus('clean');
        setError(null);
        setHasRestoredDraft(false);
      }
    }
    prevServerContentRef.current = serverContent;
  }

  // ── Deferred localStorage I/O ─────────────────────────────────
  //
  // Runs after commit, so a discarded render can never touch storage.
  // Handles both the file-switch draft *restore* (Case 1) and the
  // phantom-dirty *clear* (Case 2B) queued by the render body above.
  useEffect(() => {
    if (pendingClearRef.current) {
      pendingClearRef.current = false;
      clearDraft();
    }
    const restoreKey = pendingRestoreKeyRef.current;
    if (restoreKey !== null) {
      pendingRestoreKeyRef.current = null;
      const stored = readDraft();
      if (stored !== null && !isEqual(stored, serverContent)) {
        setDraftState(stored);
        setStatus('dirty');
        setError(null);
        setHasRestoredDraft(true);
        setLastEditedAt(Date.now());
      } else if (stored !== null) {
        // Stored draft already matches server (e.g. saved in another tab).
        clearDraft();
      }
    }
  }, [fileKey, serverContent, clearDraft, readDraft, isEqual]);

  // ── Public actions ───────────────────────────────────────────

  const setDraft = useCallback(
    (next: T) => {
      setDraftState(next);
      setLastEditedAt(Date.now());
      setError(null);
      // Edits during a 'saved' flash demote us back to 'dirty' —
      // the new value isn't on the server.
      setStatus((prev) => {
        if (prev === 'saving') return prev; // Don't interrupt in-flight save.
        if (isEqual(next, serverContent)) return 'clean';
        return 'dirty';
      });
      writer.schedule(next);
    },
    [isEqual, serverContent, writer],
  );

  const save = useCallback(async () => {
    // Guard against accidental double-commit on Cmd+S spam, save
    // button mash, etc.
    if (statusRef.current !== 'dirty' && statusRef.current !== 'error') {
      return;
    }
    if (flashTimerRef.current) {
      clearTimeout(flashTimerRef.current);
      flashTimerRef.current = null;
    }
    setStatus('saving');
    statusRef.current = 'saving';
    setError(null);
    const snapshot = draftRef.current;
    const generation = fileGeneration.current;
    writer.flush();
    try {
      await saveFn(snapshot);
      if (!mountedRef.current || generation !== fileGeneration.current) {
        // A completed write still belongs to its original file. Never mark
        // the newly opened editor clean or clear its local recovery draft.
        const persisted = readDraft();
        if (persisted !== null && isEqual(persisted, snapshot)) clearDraft();
        return;
      }
      // Race check: if the user typed during the in-flight save, we
      // should NOT mark clean — there's now a newer draft that
      // hasn't been saved. Demote to 'dirty' and let them save
      // again.
      const stillCurrent = isEqual(draftRef.current, snapshot);
      if (stillCurrent) {
        clearDraft();
        setStatus('saved');
        setLastSavedAt(Date.now());
        flashTimerRef.current = setTimeout(() => {
          setStatus('clean');
          flashTimerRef.current = null;
        }, STATUS_FLASH_MS);
      } else {
        // Their newer draft is what we want to surface.
        setLastSavedAt(Date.now());
        setStatus('dirty');
      }
    } catch (err) {
      console.error('[useManualSave] Save failed:', err);
      if (!mountedRef.current || generation !== fileGeneration.current) return;
      setError(err instanceof Error ? err.message : String(err));
      setStatus('error');
    }
  }, [saveFn, isEqual, clearDraft, readDraft, writer]);

  const discard = useCallback(() => {
    if (flashTimerRef.current) {
      clearTimeout(flashTimerRef.current);
      flashTimerRef.current = null;
    }
    clearDraft();
    setDraftState(serverContent);
    setStatus('clean');
    setError(null);
    setHasRestoredDraft(false);
    setLastEditedAt(null);
  }, [clearDraft, serverContent]);

  const acknowledgeRestoredDraft = useCallback(() => {
    setHasRestoredDraft(false);
  }, []);

  return {
    draft,
    setDraft,
    status,
    dirty: status === 'dirty' || status === 'error',
    error,
    save,
    discard,
    hasRestoredDraft,
    acknowledgeRestoredDraft,
    lastEditedAt,
    lastSavedAt,
  };
}
