import { ResponsiveWorkspaceProvider } from '@/features/workspace/responsive';
import { act, fireEvent, render as renderUI, renderHook, screen } from '@testing-library/react';
import { useState, type ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { ProjectSessionProvider, selectFilePanel, useSessionValue, useProjectSession, useWorkspaceChat } from '@/features/workspace/session';
import { ProjectAuxiliaryActions } from '@/components/project/ProjectAuxiliaryActions';
import { changedFolders, isCommitInvalidationKey } from '@/lib/queryKeys';

describe('project-owned session', () => {
  it('retains selection on view unmount, resets on a different project', () => {
    function View() { const [value, setValue] = useSessionValue('historyCommit'); return <button onClick={() => setValue('commit-a')}>{value ?? 'empty'}</button>; }
    function Shell({ id, view }: { id: string; view: boolean }) { return <ProjectSessionProvider projectId={id}>{view && <View />}</ProjectSessionProvider>; }
    const ui = render(<Shell id='a' view />);
    act(() => screen.getByRole('button').click());
    ui.rerender(<Shell id='a' view={false} />); ui.rerender(<Shell id='a' view />);
    expect(screen.getByRole('button').textContent).toBe('commit-a');
    ui.rerender(<Shell id='b' view />);
    expect(screen.getByRole('button').textContent).toBe('empty');
  });
  it('selectors do not rerender unrelated consumers and actions stay stable', () => {
    const wrapper = ({ children }: { children: ReactNode }) => <ProjectSessionProvider projectId='a'>{children}</ProjectSessionProvider>;
    let renders = 0;
    let mutate: (value: string) => void = () => {};
    function Writer() { const [, set] = useSessionValue('historyScope'); mutate = set; return null; }
    const combined = ({ children }: { children: ReactNode }) => wrapper({ children: <><Writer />{children}</> });
    const hook = renderHook(() => { renders++; return useProjectSession(s => s.closePanel); }, { wrapper: combined });
    const action = hook.result.current, baseline = renders;
    act(() => mutate('docs'));
    expect(hook.result.current).toBe(action); expect(renders).toBe(baseline);
  });
  it('chat state survives route content replacement', () => {
    function Chat() { const [open, set] = useWorkspaceChat(); const [draft, update] = useState(''); return <><button onClick={() => set(true)}>chat</button>{open && <input value={draft} onChange={e => update(e.target.value)} />}</>; }
    const ui = render(<ProjectSessionProvider projectId='a'><Chat /><div>Files</div></ProjectSessionProvider>);
    act(() => screen.getByText('chat').click());
    const input = screen.getByRole('textbox');
    ui.rerender(<ProjectSessionProvider projectId='a'><Chat /><div>Git</div></ProjectSessionProvider>);
    expect(screen.getByRole('textbox')).toBe(input);
  });
  it('switches Access and Chat in one mutually-exclusive project panel slot', () => {
    render(<ProjectSessionProvider projectId='a'><ProjectAuxiliaryActions /></ProjectSessionProvider>);
    const access = screen.getByRole('button', { name: 'Access' });
    const chat = screen.getByRole('button', { name: 'Chat' });
    act(() => access.click());
    expect(access.getAttribute('aria-pressed')).toBe('true');
    expect(chat.getAttribute('aria-pressed')).toBe('false');
    act(() => chat.click());
    expect(access.getAttribute('aria-pressed')).toBe('false');
    expect(chat.getAttribute('aria-pressed')).toBe('true');
    act(() => chat.click());
    expect(chat.getAttribute('aria-pressed')).toBe('false');
  });
  it('keeps the active auxiliary panel when its navigation guard rejects leaving', () => {
    const hook = renderHook(() => useProjectSession(state => state), { wrapper: ({ children }) => (
      <ProjectSessionProvider projectId='a'>{children}</ProjectSessionProvider>
    ) });
    act(() => hook.result.current.openPanel({ type: 'access_list', view: 'detail' }));
    act(() => hook.result.current.setPanelNavigationGuard(() => false));
    act(() => hook.result.current.openPanel({ type: 'workspace_chat' }));
    expect(hook.result.current.panel.type).toBe('access_list');
    act(() => hook.result.current.setPanelNavigationGuard(() => true));
    act(() => hook.result.current.openPanel({ type: 'workspace_chat' }));
    expect(hook.result.current.panel.type).toBe('workspace_chat');
    expect(hook.result.current.panelNavigationGuard).toBeNull();
  });
});

it('invalidates affected ancestors but never immutable versions or another project', () => {
  const folders = changedFolders(['docs/a/b.md']);
  expect([...folders]).toEqual(['', 'docs', 'docs/a']);
  expect(isCommitInvalidationKey(['tree', 'p', 'docs'], 'p', folders)).toBe(true);
  expect(isCommitInvalidationKey(['tree', 'p', 'other'], 'p', folders)).toBe(false);
  expect(isCommitInvalidationKey(['project-history', 'q'], 'p', folders)).toBe(false);
  expect(isCommitInvalidationKey(['version-preview', 'p', 'a', 'oid'], 'p', folders)).toBe(false);
});

function render(ui: React.ReactElement) { return renderUI(ui, { wrapper: ResponsiveWorkspaceProvider }); }


it('keeps project sidebars out of the file inspector subscription', () => {
  let fileRenders = 0;
  function FileInspector() {
    const panel = useProjectSession(selectFilePanel);
    fileRenders++;
    return <output data-testid='file-panel'>{panel.type}:{panel.nodeId}</output>;
  }
  function Controls() {
    const open = useProjectSession(state => state.openPanel);
    const close = useProjectSession(state => state.closePanel);
    return <><button onClick={() => open({ type: 'workspace_chat' })}>Agent</button><button onClick={() => open({ type: 'access_list' })}>Access</button><button onClick={close}>Close</button><button onClick={() => open({ type: 'version_history', nodeId: 'notes.md' })}>History</button></>;
  }
  render(<ProjectSessionProvider projectId='p'><FileInspector /><Controls /></ProjectSessionProvider>);
  fireEvent.click(screen.getByText('Agent'));
  fireEvent.click(screen.getByText('Access'));
  fireEvent.click(screen.getByText('Close'));
  expect(fileRenders).toBe(1);
  fireEvent.click(screen.getByText('History'));
  expect(screen.getByTestId('file-panel').textContent).toBe('version_history:notes.md');
  expect(fileRenders).toBe(2);
  fireEvent.click(screen.getByText('Agent'));
  expect(screen.getByTestId('file-panel').textContent).toBe('none:');
});
