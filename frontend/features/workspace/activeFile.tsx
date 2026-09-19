'use client';

import { createContext, useContext, useEffect, useState, type ReactNode, type Dispatch, type SetStateAction } from 'react';

// A live view projection for Chat mentions/refresh, not another data cache.
// The Files surface supplies it while mounted; unmount clears the projection.
type ActiveFile = { tableId?: string; tableData?: unknown; onDataUpdate?: (data: unknown) => void };
const EMPTY: ActiveFile = {};
const Value = createContext<ActiveFile>(EMPTY);
const Actions = createContext<Dispatch<SetStateAction<ActiveFile>> | null>(null);

export function ActiveFileProvider({ children }: { children: ReactNode }) {
  const [file, setFile] = useState<ActiveFile>(EMPTY);
  return <Actions.Provider value={setFile}><Value.Provider value={file}>{children}</Value.Provider></Actions.Provider>;
}

export function useActiveFile() { return useContext(Value); }

export function usePublishActiveFile({ tableId, tableData, onDataUpdate }: ActiveFile) {
  const publish = useContext(Actions);
  useEffect(() => {
    publish?.({ tableId, tableData, onDataUpdate });
    return () => publish?.(EMPTY);
  }, [publish, tableId, tableData, onDataUpdate]);
}
