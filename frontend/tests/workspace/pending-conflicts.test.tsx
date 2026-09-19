import { renderHook, waitFor } from '@testing-library/react';
import { type ReactNode } from 'react';
import { SWRConfig } from 'swr';
import { expect, it, vi } from 'vitest';
const list = vi.hoisted(() => vi.fn());
vi.mock('@/lib/conflictApi', () => ({ listPendingConflicts: list }));
import { usePendingItems } from '@/features/history/pendingConflicts';
it('conflict and review selectors share one request without sharing results across projects', async () => {
  list.mockResolvedValue([
    { pending_conflict_id: 'human', status: 'pending', resolver_kind: 'human', policy: 'manual_review' },
    { pending_conflict_id: 'agent', status: 'pending', resolver_kind: 'agent', policy: 'agent_review' },
  ]);
  const cache = new Map();
  const wrapper = ({ children }: { children: ReactNode }) => <SWRConfig value={{ provider: () => cache }}>{children}</SWRConfig>;
  const hook = renderHook(({ project }) => ({
    conflict: usePendingItems(project, 'conflict', true), review: usePendingItems(project, 'pending-review', true),
  }), { wrapper, initialProps: { project: 'a' } });
  await waitFor(() => expect(hook.result.current.conflict.data).toHaveLength(1));
  expect(hook.result.current.review.data?.[0].id).toBe('agent'); expect(list).toHaveBeenCalledTimes(1);
  list.mockReturnValue(new Promise(() => {})); hook.rerender({ project: 'b' });
  expect(hook.result.current.conflict.data).toBeUndefined();
});
