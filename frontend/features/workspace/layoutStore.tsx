'use client';

import { createContext, useContext, useEffect, useLayoutEffect, useState, type ReactNode } from 'react';
import { createStore } from 'zustand/vanilla';
import { useStore } from 'zustand';
import { DEFAULT_WORKSPACE_INPUT, resolveWorkspaceLayout, type WorkspaceLayout, type WorkspaceLayoutInput } from './paneLayout';

type PaneName = 'projects' | 'files' | 'auxiliary' | 'inspector';
type LayoutState = {
  input: WorkspaceLayoutInput;
  layout: WorkspaceLayout;
  setEnvironment: (environment: Pick<WorkspaceLayoutInput, 'availableWidth' | 'segments'>) => void;
  setPane: <K extends PaneName>(name: K, patch: Partial<WorkspaceLayoutInput[K]>) => void;
};
export function createWorkspaceLayoutStore(initial: Partial<WorkspaceLayoutInput> = {}) {
  const input = { ...DEFAULT_WORKSPACE_INPUT, ...initial };
  return createStore<LayoutState>((set) => ({
    input,
    layout: resolveWorkspaceLayout(input),
    setEnvironment: environment => set(state => {
      if (state.input.availableWidth === environment.availableWidth
        && JSON.stringify(state.input.segments) === JSON.stringify(environment.segments)) return state;
      const input = { ...state.input, ...environment };
      return { input, layout: resolveWorkspaceLayout(input) };
    }),
    setPane: (name, patch) => set(state => {
      if (Object.entries(patch).every(([key, value]) => state.input[name][key as keyof typeof patch] === value)) return state;
      const input = { ...state.input, [name]: { ...state.input[name], ...patch } };
      return { input, layout: resolveWorkspaceLayout(input) };
    }),
  }));
}
export type WorkspaceLayoutStore = ReturnType<typeof createWorkspaceLayoutStore>;
const Context = createContext<WorkspaceLayoutStore | null>(null);
export function WorkspaceLayoutProvider({ children, store: provided }: { children: ReactNode; store?: WorkspaceLayoutStore }) {
  const [store] = useState(() => provided ?? createWorkspaceLayoutStore());
  return <Context.Provider value={store}>{children}</Context.Provider>;
}
export function useWorkspaceLayoutStore() {
  const store = useContext(Context);
  if (!store) throw new Error('Workspace layout requires ResponsiveWorkspaceProvider');
  return store;
}
export function useWorkspaceLayout<T>(selector: (layout: WorkspaceLayout) => T) {
  return useStore(useWorkspaceLayoutStore(), state => selector(state.layout));
}
const useBeforePaint = typeof window === 'undefined' ? useEffect : useLayoutEffect;
/** Register presence separately from preference updates; resize never unmounts
 * a region or briefly unregisters it. Only one owner registers each pane. */
export function useWorkspacePane<K extends PaneName>(name: K, pane: WorkspaceLayoutInput[K]) {
  const store = useWorkspaceLayoutStore();
  const { preferredWidth, minWidth, maxWidth, present } = pane;
  const collapsed = 'collapsed' in pane ? pane.collapsed : undefined;
  const collapsedWidth = 'collapsedWidth' in pane ? pane.collapsedWidth : undefined;
  const open = 'open' in pane ? pane.open : undefined;
  useBeforePaint(() => {
    store.getState().setPane(name, pane);
    // All primitive contract fields are explicit dependencies. Object identity
    // is intentionally irrelevant to registration.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store, name, preferredWidth, minWidth, maxWidth, present, collapsed, collapsedWidth, open]);
  useBeforePaint(() => () => store.getState().setPane<PaneName>(name, { present: false }), [store, name]);
}
