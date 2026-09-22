import { act, renderHook } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { useEditorSaveSession } from '@/lib/hooks/useEditorSaveSession';

it('cannot write a placeholder/failed load, but preserves the draft for recovery', async () => {
  const saveContent = vi.fn().mockResolvedValue(undefined);
  const hook = renderHook(({ ready }) => useEditorSaveSession({ projectId: 'p', filePath: 'note.md', nodeType: 'markdown', serverContent: '', isContentReady: ready, saveContent }), { initialProps: { ready: false } });
  act(() => hook.result.current.onChange('restored draft'));
  await act(async () => { await hook.result.current.save(); });
  expect(saveContent).not.toHaveBeenCalled();
  expect(hook.result.current.error).toContain('before file content has loaded');
  expect(hook.result.current.content).toBe('restored draft');
  hook.rerender({ ready: true });
  await act(async () => { await hook.result.current.save(); });
  expect(saveContent).toHaveBeenCalledExactlyOnceWith('restored draft');
});
