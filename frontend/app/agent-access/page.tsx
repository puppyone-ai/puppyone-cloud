'use client';

import { useCallback } from 'react';
import { AccessInvitation } from '@/features/access/lifecycle/AccessInvitation';

export default function AgentAccessPage() {
  const consumeFragment = useCallback(() => {
    window.history.replaceState(window.history.state, '', '/agent-access');
  }, []);
  return <AccessInvitation onConsumeFragment={consumeFragment} />;
}
