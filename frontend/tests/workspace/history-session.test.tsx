import { act, renderHook } from '@testing-library/react';
import { StrictMode, type ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { useHistoryReconciliation } from '@/features/history/useHistoryReconciliation';
import { ProjectSessionProvider, useProjectSession } from '@/features/workspace/session';

const wrapper = ({ children }: { children: ReactNode }) => (
  <StrictMode><ProjectSessionProvider projectId='p'>{children}</ProjectSessionProvider></StrictMode>
);
const empty = {
  loaded: false,
  scopeOptions: [] as { scope: string }[],
  actorOptions: [] as { type: string }[],
  filteredCommits: [] as { commit_id: string }[],
  headCommitId: '',
  needsActionSelected: false,
};

describe('history snapshot reconciliation', () => {
  it('retains filters, expansion and selection during loading, then keeps a valid selection', () => {
    const hook = renderHook(props => {
      useHistoryReconciliation(props);
      return useProjectSession(state => state);
    }, { wrapper, initialProps: empty });
    act(() => {
      hook.result.current.setValue('historyScope', 'docs');
      hook.result.current.setValue('historyActor', 'agent');
      hook.result.current.setValue('historyCommit', 'older');
      hook.result.current.setValue('historyExpanded', true);
    });
    hook.rerender({ ...empty });
    expect(hook.result.current).toMatchObject({ historyScope: 'docs', historyActor: 'agent', historyCommit: 'older', historyExpanded: true });
    hook.rerender({ ...empty, loaded: true, scopeOptions: [{ scope: 'docs' }], actorOptions: [{ type: 'agent' }],
      filteredCommits: [{ commit_id: 'head' }, { commit_id: 'older' }], headCommitId: 'head' });
    expect(hook.result.current).toMatchObject({ historyScope: 'docs', historyActor: 'agent', historyCommit: 'older', historyExpanded: true });
    hook.rerender({ ...empty, loaded: true });
    expect(hook.result.current).toMatchObject({ historyScope: '', historyActor: null, historyCommit: null, historyExpanded: false });
  });

  it('selects HEAD only if visible and does not steal a needs-action selection', () => {
    const snapshot = { ...empty, loaded: true, filteredCommits: [{ commit_id: 'a' }, { commit_id: 'head' }], headCommitId: 'head' };
    const hook = renderHook(props => {
      useHistoryReconciliation(props);
      return useProjectSession(state => state);
    }, { wrapper, initialProps: snapshot });
    expect(hook.result.current.historyCommit).toBe('head');
    hook.rerender({ ...snapshot, filteredCommits: [{ commit_id: 'a' }] });
    expect(hook.result.current.historyCommit).toBe('a');
    hook.rerender({ ...snapshot, needsActionSelected: true });
    act(() => hook.result.current.setValue('historyCommit', null));
    expect(hook.result.current.historyCommit).toBeNull();
    hook.rerender(snapshot);
    expect(hook.result.current.historyCommit).toBe('head');
  });

  it('collapses only on changed filters, not equivalent values or rerenders', () => {
    const hook = renderHook(() => useProjectSession(state => state), { wrapper });
    act(() => hook.result.current.setValue('historyExpanded', true));
    act(() => hook.result.current.setValue('historyScope', ''));
    expect(hook.result.current.historyExpanded).toBe(true);
    act(() => hook.result.current.setValue('historyActor', 'agent'));
    expect(hook.result.current.historyExpanded).toBe(false);
    act(() => hook.result.current.setValue('historyExpanded', true));
    hook.rerender();
    expect(hook.result.current.historyExpanded).toBe(true);
  });
});
