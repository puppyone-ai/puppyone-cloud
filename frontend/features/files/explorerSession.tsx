'use client';

import { createContext, useContext, useState, type ReactNode } from 'react';
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';

export type PendingCreatingInfo = { label: string };
export function createExplorerSession() {
  return createStore(() => ({ expanded: new Set<string>(), creating: new Map<string, PendingCreatingInfo>() }));
}
type Store = ReturnType<typeof createExplorerSession>;
const Context = createContext<Store | null>(null);
const RegistryContext = createContext<Map<string, Store> | null>(null);
/** Bounded account-owned memory preserves the existing return-to-project tree
 * expansion behavior, without leaking a module-global map across accounts. */
export function ExplorerSessionsProvider({ children }: { children: ReactNode }) {
  const [sessions] = useState(() => new Map<string, Store>());
  return <RegistryContext.Provider value={sessions}>{children}</RegistryContext.Provider>;
}
export function ExplorerSessionProvider({ projectId = 'standalone', children }: { projectId?: string; children: ReactNode }) {
  const registry = useContext(RegistryContext);
  const [store] = useState(() => {
    const session = registry?.get(projectId) ?? createExplorerSession();
    if (registry) {
      registry.delete(projectId);
      registry.set(projectId, session);
      if (registry.size > 16) registry.delete(registry.keys().next().value!);
    }
    return session;
  });
  return <Context.Provider value={store}>{children}</Context.Provider>;
}
function useExplorerStore() {
  const store = useContext(Context);
  if (!store) throw new Error('Explorer requires ExplorerSessionProvider');
  return store;
}
const key = (project: string, path: string) => JSON.stringify([project, path]);
export function useExplorerActions() {
  const store = useExplorerStore();
  const [actions] = useState(() => {
    const ensureExpandedBatch = (project: string, paths: string[]) => store.setState(state => {
      const ids = paths.map(path => key(project, path));
      if (ids.every(id => state.expanded.has(id))) return state;
      return { expanded: new Set([...state.expanded, ...ids]) };
    });
    const addPendingCreatingNode = (project: string, path: string, info: PendingCreatingInfo) => store.setState(state => ({ creating: new Map(state.creating).set(key(project, path), info) }));
    return {
      ensureExpandedBatch,
      ensureExpanded: (project: string, path: string) => ensureExpandedBatch(project, [path]),
      toggleExpanded: (project: string, path: string) => store.setState(state => {
        const expanded = new Set(state.expanded); const id = key(project, path);
        if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
        return { expanded };
      }),
      addPendingCreatingNode,
      addPendingCreatingPath: (project: string, path: string) => addPendingCreatingNode(project, path, { label: 'Creating folder' }),
      removePendingCreatingPath: (project: string, path: string) => store.setState(state => {
        if (!state.creating.has(key(project, path))) return state;
        const creating = new Map(state.creating); creating.delete(key(project, path)); return { creating };
      }),
    };
  });
  return actions;
}
export function useIsExpanded(project: string, path: string) { return useStore(useExplorerStore(), state => state.expanded.has(key(project, path))); }
export function usePendingCreatingInfo(project: string, path: string) { return useStore(useExplorerStore(), state => state.creating.get(key(project, path)) ?? null); }
export function useIsPendingCreatingPath(project: string, path: string) { return usePendingCreatingInfo(project, path) !== null; }
