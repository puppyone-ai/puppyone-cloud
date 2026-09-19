'use client';

import { createContext, useEffect, useState, type ReactNode } from 'react';
import { createEditorSessionRegistry } from './session';

export const EditorSessionContext = createContext<ReturnType<typeof createEditorSessionRegistry> | null>(null);
/** Account-owned, bounded sessions survive Files/Git and project navigation. */
export function EditorSessionProvider({ scope, children }: { scope: string; children: ReactNode }) {
  const [registry] = useState(() => createEditorSessionRegistry(scope));
  useEffect(() => {
    return () => registry.flush();
  }, [registry]);
  return <EditorSessionContext.Provider value={registry}>{children}</EditorSessionContext.Provider>;
}
