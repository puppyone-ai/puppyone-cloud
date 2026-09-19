import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
const api = vi.hoisted(() => ({ stat: vi.fn(), read: vi.fn() }));
vi.mock('@/lib/contentTreeApi', () => ({ stat: api.stat, readFile: api.read }));
vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams() }));
import { usePathResolver } from '@/features/files/usePathResolver';
beforeEach(() => { api.stat.mockReset(); api.read.mockReset(); });
it('paints metadata before slow text and aborts old reads on file switch', async () => {
  let oldBody!: (value: unknown) => void;
  api.stat.mockResolvedValue({ exists: true, type: 'markdown', mime_type: 'text/markdown' });
  api.read.mockImplementationOnce(() => new Promise(r => { oldBody = r; })).mockResolvedValue({ content_text: 'new file' });
  const hook = renderHook(({ path }) => usePathResolver('p', [path]), { initialProps: { path: 'old.md' } });
  await waitFor(() => expect(hook.result.current.isResolvingPath).toBe(false));
  expect(hook.result.current.isLoadingText).toBe(true); expect(hook.result.current.activeNodeId).toBe('old.md');
  const signal = api.read.mock.calls[0][2] as AbortSignal;
  hook.rerender({ path: 'new.md' }); expect(signal.aborted).toBe(true);
  await waitFor(() => expect(hook.result.current.textContent).toBe('new file'));
  await act(async () => oldBody({ content_text: 'stale file' }));
  expect(hook.result.current.textContent).toBe('new file'); hook.unmount();
  expect((api.read.mock.calls[1][2] as AbortSignal).aborted).toBe(true);
});
it('does not read content for a folder hint', async () => {
  api.stat.mockResolvedValue({ exists: true, type: 'folder' });
  const hook = renderHook(() => usePathResolver('p', ['docs'], 'folder'));
  await waitFor(() => expect(hook.result.current.isResolvingPath).toBe(false));
  expect(api.read).not.toHaveBeenCalled(); expect(hook.result.current.currentFolderId).toBe('docs');
});

it('exposes a failed body read instead of treating it as an empty file or retrying twice', async () => {
  api.stat.mockResolvedValue({ exists: true, type: 'markdown' });
  api.read.mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ content_text: 'recovered' });
  const hook = renderHook(() => usePathResolver('p', ['note.md'], 'markdown'));
  await waitFor(() => expect(hook.result.current.readError?.message).toBe('offline'));
  expect(api.read).toHaveBeenCalledTimes(1);
  act(() => hook.result.current.retryRead());
  await waitFor(() => expect(hook.result.current.textContent).toBe('recovered'));
  expect(hook.result.current.readError).toBeNull();
});

it('a failed stat is not interpreted as an empty root folder', async () => {
  api.stat.mockRejectedValue(new Error('no access'));
  const hook = renderHook(() => usePathResolver('p', ['docs'], 'folder'));
  await waitFor(() => expect(hook.result.current.readError?.message).toBe('no access'));
  expect(hook.result.current.currentFolderId).toBe('docs');
  expect(api.read).not.toHaveBeenCalled();
});
