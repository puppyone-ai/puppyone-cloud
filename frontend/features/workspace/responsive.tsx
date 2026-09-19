'use client';

import { createContext, useContext, useState, type ReactNode } from 'react';
import { createStore } from 'zustand/vanilla';
import { useStore } from 'zustand';
import { WorkspaceViewport } from './WorkspaceViewport';
import { WorkspaceRegionsProvider } from './regions';
import { WorkspaceLayoutProvider, useWorkspaceLayout, type WorkspaceLayoutStore } from './layoutStore';

export function useFileDrawer() {
  return useWorkspaceLayout(layout => layout.files.presentation === 'overlay');
}

type NavigationState = {
  projectsOpen: boolean;
  filesOpen: boolean;
  actions: {
    setProjectsOpen: (open: boolean) => void;
    setFilesOpen: (open: boolean) => void;
    closeNavigation: () => void;
  };
};
const createNavigation = () => createStore<NavigationState>(set => ({
  projectsOpen: false,
  filesOpen: false,
  actions: {
    setProjectsOpen: open => set(state => ({ projectsOpen: open, filesOpen: open ? false : state.filesOpen })),
    setFilesOpen: open => set(state => ({ filesOpen: open, projectsOpen: open ? false : state.projectsOpen })),
    closeNavigation: () => set({ projectsOpen: false, filesOpen: false }),
  },
}));
const NavigationContext = createContext<ReturnType<typeof createNavigation> | null>(null);

/** User intent survives resize; no breakpoint effect mutates navigation state. */
export function ResponsiveWorkspaceProvider({ children, layoutStore }: { children: ReactNode; layoutStore?: WorkspaceLayoutStore }) {
  const [store] = useState(createNavigation);
  return <NavigationContext.Provider value={store}>
    <WorkspaceViewport />
    <WorkspaceLayoutProvider store={layoutStore}><WorkspaceRegionsProvider>{children}</WorkspaceRegionsProvider></WorkspaceLayoutProvider>
  </NavigationContext.Provider>;
}
export function useWorkspaceNavigation<T>(selector: (state: NavigationState) => T): T {
  const store = useContext(NavigationContext);
  if (!store) throw new Error('Workspace navigation requires ResponsiveWorkspaceProvider');
  return useStore(store, selector);
}
export function useWorkspaceActions() { return useWorkspaceNavigation(state => state.actions); }
