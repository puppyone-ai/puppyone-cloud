'use client';

import { useEffect } from 'react';
import { useProjectSession } from '@/features/workspace/session';

/** `?agent=open` (from the chat-app landing page) opens the project's Agent side panel once, on `session` when given. */
export function useOpenAgentFromUrl() {
  const openPanel = useProjectSession(state => state.openPanel);
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    if (query.get('agent') === 'open') {
      openPanel({ type: 'workspace_chat', sessionId: query.get('session') || undefined });
    }
  }, [openPanel]);
}
