'use client';

import { createContext, useContext, useMemo, type ReactNode } from 'react';

type WorkspaceShellContextValue = {
  isProjectRailCollapsed: boolean;
  toggleProjectRail: () => void;
};

const WorkspaceShellContext = createContext<WorkspaceShellContextValue | null>(
  null
);

export function WorkspaceShellProvider({
  isProjectRailCollapsed,
  toggleProjectRail,
  children,
}: WorkspaceShellContextValue & { children: ReactNode }) {
  const value = useMemo(() => ({ isProjectRailCollapsed, toggleProjectRail }), [isProjectRailCollapsed, toggleProjectRail]);
  return (
    <WorkspaceShellContext.Provider
      value={value}
    >
      {children}
    </WorkspaceShellContext.Provider>
  );
}

export function useWorkspaceShell() {
  return useContext(WorkspaceShellContext);
}
