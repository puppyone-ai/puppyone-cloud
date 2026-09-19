import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const api = vi.hoisted(() => ({ token: vi.fn(), head: vi.fn(), history: vi.fn() }));
vi.mock('@/lib/apiClient', () => ({ getApiAccessToken: api.token }));
vi.mock('@/lib/contentTreeApi', () => ({ getProjectHead: api.head, getProjectHistory: api.history }));
import { subscribeVersionNotifications, _resetAllForTests } from '@/lib/versionWebSocketClient';

class Socket {
  static instances: Socket[] = [];
  onopen?: () => void; onclose?: () => void; onmessage?: (event: { data: string }) => void;
  close = vi.fn(() => this.onclose?.());
  constructor() { Socket.instances.push(this); }
}
const tick = () => vi.advanceTimersByTimeAsync(0);
const commit = (i: number) => ({ commit_id: `c${i}`, who: 'author', changes: [{ path: `docs/${i}.md` }] });
beforeEach(() => {
  vi.useFakeTimers(); vi.stubGlobal('WebSocket', Socket); vi.spyOn(Math, 'random').mockReturnValue(0);
  Socket.instances = []; api.token.mockResolvedValue('token'); api.head.mockResolvedValue({ head_commit_id: 'c0' }); api.history.mockReset();
});
afterEach(() => { _resetAllForTests(); vi.unstubAllGlobals(); vi.useRealTimers(); });
it('coalesces subscribers, reads only HEAD on first connection, tears down on last unsubscribe', async () => {
  const a = subscribeVersionNotifications('p', vi.fn()), b = subscribeVersionNotifications('p', vi.fn());
  await tick(); expect(Socket.instances).toHaveLength(1);
  Socket.instances[0].onopen?.(); await tick();
  expect(api.head).toHaveBeenCalledWith('p'); expect(api.history).not.toHaveBeenCalled();
  a(); expect(Socket.instances[0].close).not.toHaveBeenCalled(); b(); expect(Socket.instances[0].close).toHaveBeenCalledOnce();
});
it('cannot open a socket after unsubscribe while auth is pending', async () => {
  let resolve!: (token: string) => void;
  api.token.mockReturnValue(new Promise<string>(r => { resolve = r; }));
  const stop = subscribeVersionNotifications('p', vi.fn()); stop(); resolve('token'); await tick();
  expect(Socket.instances).toHaveLength(0);
});
it('replays more than one HTTP page and deduplicates live frames buffered during catch-up', async () => {
  const handler = vi.fn(); subscribeVersionNotifications('p', handler); await tick();
  Socket.instances[0].onopen?.(); await tick();
  api.history.mockResolvedValueOnce({ commits: Array.from({ length: 100 }, (_, i) => commit(i + 1)) })
    .mockResolvedValueOnce({ commits: [commit(101)] });
  Socket.instances[0].onclose?.(); await vi.advanceTimersByTimeAsync(1000);
  const socket = Socket.instances[1]; socket.onopen?.();
  socket.onmessage?.({ data: JSON.stringify({ type: 'commit_update', commit_id: 'c101', notification_id: 'live', changed_files: [] }) });
  await tick();
  expect(api.history.mock.calls).toEqual([['p', 100, 'c0'], ['p', 100, 'c100']]);
  expect(handler).toHaveBeenCalledTimes(101);
  expect(handler.mock.calls.map(([event]) => event.commit_id)).toEqual(Array.from({ length: 101 }, (_, i) => `c${i + 1}`));
});
it('closes a failed authoritative catch-up rather than silently accepting live events', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  subscribeVersionNotifications('p', vi.fn()); await tick(); Socket.instances[0].onopen?.(); await tick();
  api.history.mockRejectedValue(new Error('offline'));
  Socket.instances[0].onclose?.(); await vi.advanceTimersByTimeAsync(1000); Socket.instances[1].onopen?.(); await tick();
  expect(Socket.instances[1].close).toHaveBeenCalledWith(1012, 'canonical-reconciliation-failed');
});
it('detects a cursor that stalls on a later page, not only on the initial anchor', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  subscribeVersionNotifications('p', vi.fn()); await tick(); Socket.instances[0].onopen?.(); await tick();
  api.history.mockResolvedValue({ commits: Array.from({ length: 100 }, (_, i) => commit(i + 1)) });
  Socket.instances[0].onclose?.(); await vi.advanceTimersByTimeAsync(1000); Socket.instances[1].onopen?.(); await tick();
  expect(api.history).toHaveBeenCalledTimes(2); expect(Socket.instances[1].close).toHaveBeenCalledWith(1012, 'canonical-reconciliation-failed');
});
