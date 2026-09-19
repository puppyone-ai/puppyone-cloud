import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useManualSave } from '@/features/files/useManualSave';
const serialize = (value: string) => value;
const deserialize = serialize;
const key = (file: string) => `puppyone:editor-draft:${file}`;
const defaults = { fileKey: 'p:a', serverContent: 'server', serialize, deserialize, save: async () => {} };
afterEach(() => { vi.useRealTimers(); });
it('coalesces keystrokes and flushes latest draft on unmount', () => {
  vi.useFakeTimers(); const spy = vi.spyOn(Storage.prototype, 'setItem');
  const hook = renderHook(() => useManualSave(defaults));
  act(() => { hook.result.current.setDraft('one'); hook.result.current.setDraft('two'); });
  expect(spy).not.toHaveBeenCalled(); hook.unmount();
  expect(spy).toHaveBeenCalledTimes(1); expect(JSON.parse(localStorage.getItem(key('p:a'))!).payload).toBe('two');
});
it('flushes when backgrounded and restores a draft on first mount', () => {
  const hook = renderHook(() => useManualSave(defaults));
  act(() => hook.result.current.setDraft('recover me'));
  act(() => window.dispatchEvent(new Event('pagehide'))); hook.unmount();
  const next = renderHook(() => useManualSave(defaults));
  expect(next.result.current.draft).toBe('recover me'); expect(next.result.current.hasRestoredDraft).toBe(true);
});
it('isolates draft restoration by project and file', () => {
  const hook = renderHook(props => useManualSave(props), { initialProps: defaults });
  act(() => hook.result.current.setDraft('project-a edit'));
  hook.rerender({ ...defaults, fileKey: 'q:a' }); expect(hook.result.current.draft).toBe('server');
  hook.rerender(defaults); expect(hook.result.current.draft).toBe('project-a edit');
});
it('discard cancels buffered persistence', () => {
  vi.useFakeTimers(); const hook = renderHook(() => useManualSave(defaults));
  act(() => hook.result.current.setDraft('delete me')); act(() => hook.result.current.discard());
  act(() => vi.advanceTimersByTime(500)); hook.unmount(); expect(localStorage.getItem(key('p:a'))).toBeNull();
});
it('a save completing for another file cannot clear the active draft', async () => {
  let finish!: () => void;
  const save = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
  const hook = renderHook(props => useManualSave(props), { initialProps: { ...defaults, save } });
  act(() => hook.result.current.setDraft('save a'));
  let saving!: Promise<void>; act(() => { saving = hook.result.current.save(); });
  hook.rerender({ ...defaults, save, fileKey: 'p:b' }); act(() => hook.result.current.setDraft('edit b'));
  await act(async () => { finish(); await saving; });
  expect(hook.result.current.dirty).toBe(true); expect(hook.result.current.draft).toBe('edit b');
});
it('an edit during a save remains dirty and persisted', async () => {
  let finish!: () => void;
  const hook = renderHook(() => useManualSave({ ...defaults, save: () => new Promise<void>(r => { finish = r; }) }));
  act(() => hook.result.current.setDraft('snapshot'));
  let saving!: Promise<void>; act(() => { saving = hook.result.current.save(); });
  act(() => hook.result.current.setDraft('newer'));
  await act(async () => { finish(); await saving; });
  expect(hook.result.current.dirty).toBe(true); hook.unmount();
  expect(JSON.parse(localStorage.getItem(key('p:a'))!).payload).toBe('newer');
});

it('an unmounted save cannot erase a newer draft from a remounted editor', async () => {
  let finish!: () => void;
  const old = renderHook(() => useManualSave({ ...defaults, save: () => new Promise<void>(r => { finish = r; }) }));
  act(() => old.result.current.setDraft('old snapshot'));
  let saving!: Promise<void>; act(() => { saving = old.result.current.save(); }); old.unmount();
  const current = renderHook(() => useManualSave(defaults));
  act(() => current.result.current.setDraft('new editor draft'));
  act(() => window.dispatchEvent(new Event('pagehide')));
  await act(async () => { finish(); await saving; });
  expect(current.result.current.draft).toBe('new editor draft');
  expect(JSON.parse(localStorage.getItem(key('p:a'))!).payload).toBe('new editor draft');
});
