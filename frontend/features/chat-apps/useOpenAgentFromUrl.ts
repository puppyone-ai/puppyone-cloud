'use client';

import { useEffect } from 'react';
import { useProjectSession } from '@/features/workspace/session';

/** `?agent=open` (from the chat-app landing page) opens the project's Agent side panel once. */
export function useOpenAgentFromUrl() {
  const openPanel = useProjectSession(state => state.openPanel);
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('agent') === 'open') {
      openPanel({ type: 'workspace_chat' });
    }
  }, [openPanel]);
}
