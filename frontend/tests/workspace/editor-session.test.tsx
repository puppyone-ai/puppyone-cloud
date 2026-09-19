import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { expect, it } from 'vitest';
import { EditorSessionProvider } from '@/features/files/editor/EditorSessionProvider';
import { useManualSave } from '@/features/files/useManualSave';

const identity = (s: string) => s;
it('retains a pending write and latest draft when Files unmounts and remounts during Git navigation', async () => {
  let finish!: () => void;
  const save = () => new Promise<void>(resolve => { finish = resolve; });
  function Editor() {
    const session = useManualSave({ fileKey: 'p:a', serverContent: 'server', serialize: identity, deserialize: identity, save });
    return <><input aria-label='Draft' value={session.draft} onChange={e => session.setDraft(e.target.value)} /><button onClick={() => void session.save()}>Save</button><output>{session.status}</output></>;
  }
  function Workspace() { const [files, setFiles] = useState(true); return <><button onClick={() => setFiles(v => !v)}>Switch</button>{files ? <Editor /> : <div>Git content</div>}</>; }
  render(<EditorSessionProvider scope='user'><Workspace /></EditorSessionProvider>);
  fireEvent.change(screen.getByLabelText('Draft'), { target: { value: 'snapshot' } });
  fireEvent.click(screen.getByText('Save'));
  fireEvent.click(screen.getByText('Switch')); fireEvent.click(screen.getByText('Switch'));
  expect((screen.getByLabelText('Draft') as HTMLInputElement).value).toBe('snapshot');
  expect(screen.getByText('saving')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Draft'), { target: { value: 'newer edit' } });
  await act(async () => { finish(); });
  expect(screen.getByText('dirty')).toBeTruthy();
  expect((screen.getByLabelText('Draft') as HTMLInputElement).value).toBe('newer edit');
});

it('migrates a legacy draft once and isolates it from another account', () => {
  localStorage.setItem('puppyone:editor-draft:p:a', JSON.stringify({ payload: 'legacy edit' }));
  function Editor() { const session = useManualSave({ fileKey: 'p:a', serverContent: 'server', serialize: identity, deserialize: identity, save: async () => {} }); return <output>{session.draft}</output>; }
  const first = render(<EditorSessionProvider scope='first'><Editor /></EditorSessionProvider>);
  expect(screen.getByText('legacy edit')).toBeTruthy(); first.unmount();
  render(<EditorSessionProvider scope='second'><Editor /></EditorSessionProvider>);
  expect(screen.getByText('server')).toBeTruthy();
  expect(localStorage.getItem('puppyone:editor-draft:account:first:p:a')).toContain('legacy edit');
});
