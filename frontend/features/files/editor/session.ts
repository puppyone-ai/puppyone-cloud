import { createDraftWriter } from '../draftPersistence';
import type { SaveStatus, UseManualSaveOptions } from '../useManualSave';

type Snapshot<T> = {
  draft: T; status: SaveStatus; error: string | null;
  hasRestoredDraft: boolean; lastEditedAt: number | null; lastSavedAt: number | null;
};
const normalize = (s: string) => s.replace(/[ \t]+$/gm, '').replace(/\n+$/g, '').replace(/^\n+/g, '');

/** A file owns its draft and write task even when its route has no subscriber. */
export function createEditorSession<T>(initial: UseManualSaveOptions<T>, storageKey: string, legacy?: { key: string; owner: string }) {
  let options = initial;
  let baseline = initial.serverContent;
  let lastServer = initial.serverContent;
  let restored = false;
  let subscribers = 0;
  let state: Snapshot<T> = { draft: baseline, status: 'clean', error: null, hasRestoredDraft: false, lastEditedAt: null, lastSavedAt: null };
  let flash: ReturnType<typeof setTimeout> | undefined;
  const listeners = new Set<() => void>();
  const equal = (a: T, b: T) => (options.isEqual ?? Object.is)(a, b);
  const serialize = (value: T) => options.serialize ? options.serialize(value) : JSON.stringify(value);
  const deserialize = (value: string): T => options.deserialize ? options.deserialize(value) : JSON.parse(value);
  const publish = (patch: Partial<Snapshot<T>>) => { state = { ...state, ...patch }; listeners.forEach(fn => fn()); };
  const writer = createDraftWriter<T>(value => {
    try { localStorage.setItem(storageKey, JSON.stringify({ savedAt: Date.now(), payload: serialize(value) })); } catch { /* In-memory session still works. */ }
  });
  const clear = () => { writer.cancel(); try { localStorage.removeItem(storageKey); } catch { /* Storage unavailable. */ } };
  const read = () => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return null;
      const stored = JSON.parse(raw);
      return typeof stored.payload === 'string' ? deserialize(stored.payload) : null;
    } catch { return null; }
  };

  const actions = {
    setDraft(next: T) {
      publish({ draft: next, lastEditedAt: Date.now(), error: null, status: state.status === 'saving' ? 'saving' : equal(next, baseline) ? 'clean' : 'dirty' });
      writer.schedule(next);
    },
    async save() {
      if (state.status !== 'dirty' && state.status !== 'error') return;
      clearTimeout(flash);
      const snapshot = state.draft;
      const save = options.save;
      writer.flush();
      publish({ status: 'saving', error: null });
      try {
        await save(snapshot);
        baseline = snapshot;
        const current = equal(state.draft, snapshot);
        if (current) {
          // Another owner/tab may have persisted a newer draft while this
          // instance had no subscriber. Clear only the snapshot we saved.
          const persisted = read();
          if (persisted === null || equal(persisted, snapshot)) clear();
        }
        publish({ status: current ? 'saved' : 'dirty', lastSavedAt: Date.now() });
        if (current) flash = setTimeout(() => {
          if (state.status === 'saved') publish({ status: 'clean' });
        }, 1500);
      } catch (error) {
        publish({ status: 'error', error: error instanceof Error ? error.message : String(error) });
      }
    },
    discard() {
      clearTimeout(flash); clear();
      publish({ draft: baseline, status: 'clean', error: null, hasRestoredDraft: false, lastEditedAt: null });
    },
    acknowledgeRestoredDraft() { publish({ hasRestoredDraft: false }); },
  };

  return {
    actions,
    getSnapshot: () => state,
    subscribe(listener: () => void) { listeners.add(listener); subscribers++; return () => { listeners.delete(listener); subscribers--; writer.flush(); }; },
    get retained() { return subscribers > 0 || state.status === 'saving'; },
    flush: writer.flush,
    dispose() { writer.flush(); clearTimeout(flash); },
    /** Commit effects update options; discarded React renders do no I/O. */
    configure(next: UseManualSaveOptions<T>) {
      options = next;
      if (!equal(lastServer, next.serverContent)) {
        lastServer = next.serverContent;
        if (state.status !== 'saving') {
          baseline = next.serverContent;
          if (state.status === 'clean' || state.status === 'saved') {
            const normalizedSave = state.status === 'saved' && normalize(serialize(state.draft)) === normalize(serialize(next.serverContent));
            if (!normalizedSave) publish({ draft: next.serverContent });
          } else if (equal(state.draft, next.serverContent)) {
            clear(); publish({ status: 'clean', error: null, hasRestoredDraft: false });
          }
        }
      }
      if (!restored) {
        restored = true;
        if (!next.skipDraftRestore) {
          // A one-time migration preserves existing local drafts. The caller
          // supplies a legacy key only for its recorded legacy account owner.
          if (legacy) {
            try {
              const ownerKey = 'puppyone:editor-draft:legacy-owner';
              const owner = localStorage.getItem(ownerKey);
              if (!owner) localStorage.setItem(ownerKey, legacy.owner);
              if (!owner || owner === legacy.owner) {
                const old = localStorage.getItem(legacy.key);
                if (old && !localStorage.getItem(storageKey)) { localStorage.setItem(storageKey, old); localStorage.removeItem(legacy.key); }
              }
            } catch { /* Keep legacy data if storage cannot be migrated. */ }
          }
          const stored = read();
          if (stored !== null && !equal(stored, baseline)) publish({ draft: stored, status: 'dirty', hasRestoredDraft: true, lastEditedAt: Date.now() });
          else if (stored !== null) clear();
        }
      }
    },
    /** Editors that initialize once must see new clean server text immediately. */
    preview(next: UseManualSaveOptions<T>) {
      if ((state.status === 'clean' || state.status === 'saved') && !equal(lastServer, next.serverContent)) {
        if (state.status === 'saved' && normalize(serialize(state.draft)) === normalize(serialize(next.serverContent))) return state.draft;
        return next.serverContent;
      }
      return state.draft;
    },
  };
}

export function createEditorSessionRegistry(scope?: string) {
  const entries = new Map<string, ReturnType<typeof createEditorSession<any>>>();
  return {
    get<T>(options: UseManualSaveOptions<T>) {
      const key = options.fileKey;
      let entry = entries.get(key);
      if (!entry) {
        const oldKey = `puppyone:editor-draft:${key}`;
        entry = createEditorSession(options, scope ? `puppyone:editor-draft:account:${encodeURIComponent(scope)}:${key}` : oldKey, scope && scope !== 'anonymous' ? { key: oldKey, owner: scope } : undefined);
        entries.set(key, entry);
      }
      return entry as ReturnType<typeof createEditorSession<T>>;
    },
    prune() {
      for (const [key, entry] of entries) {
        if (entries.size <= 32) break;
        if (!entry.retained) { entry.dispose(); entries.delete(key); }
      }
    },
    flush() { entries.forEach(entry => entry.flush()); },
    dispose() { entries.forEach(entry => entry.dispose()); entries.clear(); },
  };
}
