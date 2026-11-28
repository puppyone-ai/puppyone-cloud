import { renderHook } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

const openPanel = vi.fn();
vi.mock('@/features/workspace/session', () => ({ useProjectSession: (select: any) => select({ openPanel }) }));
import { useOpenAgentFromUrl } from '@/features/chat-apps/useOpenAgentFromUrl';

it('opens the Agent panel on the session from the URL', () => {
  window.history.replaceState({}, '', '/projects/p1/data?agent=open&session=s1');
  renderHook(() => useOpenAgentFromUrl());
  expect(openPanel).toHaveBeenCalledWith({ type: 'workspace_chat', sessionId: 's1' });
});

it('opens only the panel without a session, and nothing without agent=open', () => {
  window.history.replaceState({}, '', '/projects/p1/data?agent=open');
  renderHook(() => useOpenAgentFromUrl());
  expect(openPanel).toHaveBeenLastCalledWith({ type: 'workspace_chat', sessionId: undefined });
  openPanel.mockClear();
  window.history.replaceState({}, '', '/projects/p1/data');
  renderHook(() => useOpenAgentFromUrl());
  expect(openPanel).not.toHaveBeenCalled();
});
