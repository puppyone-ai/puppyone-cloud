'use client';

import { createContext, createRef, useContext, useState, type ReactNode } from 'react';

function createRegions() {
  return {
    appContent: createRef<HTMLElement>(),
    projectBody: createRef<HTMLDivElement>(),
    editor: createRef<HTMLDivElement>(),
    auxiliary: createRef<HTMLDivElement>(),
    inspector: createRef<HTMLDivElement>(),
    header: createRef<HTMLElement>(),
  };
}
const RegionsContext = createContext<ReturnType<typeof createRegions> | null>(null);

/** Stable refs describe ownership; overlays never infer it by walking the DOM. */
export function WorkspaceRegionsProvider({ children }: { children: ReactNode }) {
  const [regions] = useState(createRegions);
  return <RegionsContext.Provider value={regions}>{children}</RegionsContext.Provider>;
}
export function useWorkspaceRegions() {
  const regions = useContext(RegionsContext);
  if (!regions) throw new Error('Workspace regions require ResponsiveWorkspaceProvider');
  return regions;
}
