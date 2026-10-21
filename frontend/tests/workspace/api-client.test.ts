import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const auth = vi.hoisted(() => ({ getSession: vi.fn(), refreshSession: vi.fn(), onAuthStateChange: vi.fn(), signOut: vi.fn() }));
vi.mock('@supabase/ssr', () => ({ createBrowserClient: () => ({ auth }) }));
vi.mock('@puppyone/cloud-core', () => ({ REPOSITORY_TARGET_CONTRACT_HEADER: 'x-test-contract', REPOSITORY_TARGET_CONTRACT_VERSION: 'test' }));
const session = { access_token: 'test-token', expires_at: 9_999_999_999 };
beforeEach(() => {
  vi.resetModules(); vi.useFakeTimers();
  vi.stubEnv('NEXT_PUBLIC_SUPABASE_URL', 'https://example.invalid'); vi.stubEnv('NEXT_PUBLIC_SUPABASE_ANON_KEY', 'test');
  auth.getSession.mockResolvedValue({ data: { session } });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
it('includes time spent waiting for shared auth in the deadline', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  auth.getSession.mockReturnValue(new Promise(() => {}));
  const { apiRequest } = await import('@/lib/apiClient');
  const assertion = expect(apiRequest('/api/v1/test', { timeoutMs: 100 })).rejects.toMatchObject({ name: 'TimeoutError' });
  await vi.advanceTimersByTimeAsync(100); await assertion; expect(fetch).not.toHaveBeenCalled();
});
it.each(['apiRequest', 'apiRawRequest'] as const)('%s keeps the timer through a stalled JSON body even with a caller signal', async method => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => new Promise(() => {}) }));
  const apiRequest = (await import('@/lib/apiClient'))[method];
  const signal = new AbortController().signal;
  const assertion = expect(apiRequest('/api/v1/test', { signal, timeoutMs: 100 })).rejects.toMatchObject({ name: 'TimeoutError' });
  await vi.advanceTimersByTimeAsync(100); await assertion;
});

it('reads the actual workspace JSON contract through authenticated transport', async () => {
  const workspace = { id: 'w', project_id: 'p', generation: 'g', state: 'running', target_ref: 'refs/heads/main' };
  const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
  const fetch = vi.fn()
    .mockResolvedValueOnce(reply({ workspace: null }))
    .mockResolvedValueOnce(reply({}, 401))
    .mockResolvedValueOnce(reply({ workspace }))
    .mockResolvedValueOnce(reply({ entries: [{ name: 'note.md', kind: 'file', size: 5, modified_ns: '1' }] }))
    .mockResolvedValueOnce(reply({ name: 'note.md', kind: 'file', content_base64: btoa('saved'), revision: 'base' }))
    .mockResolvedValueOnce(reply({ revision: 'next' }));
  vi.stubGlobal('fetch', fetch);
  auth.refreshSession.mockResolvedValue({ data: { session: { ...session, access_token: 'refreshed' } } });
  const { workspaceStatus, createWorkspaceFiles } = await import('@/lib/workspaceFiles');
  expect(await workspaceStatus('p')).toEqual({ workspace: null });
  expect(await workspaceStatus('p')).toEqual({ workspace });
  const files = createWorkspaceFiles(workspace);
  expect((await files.listDir('p')).nodes[0].path).toBe('note.md');
  expect((await files.readFile('p', 'note.md')).content_text).toBe('saved');
  expect((await files.writeFile('p', 'note.md', 'changed', 'markdown')).commit_id).toBe('');
  expect(fetch).toHaveBeenCalledTimes(6);
  expect(fetch.mock.calls[2][1].headers.Authorization).toBe('Bearer refreshed');
  expect(JSON.parse(fetch.mock.calls[5][1].body).expected_revision).toBe('base');
  expect(fetch.mock.calls.every(([url]) => url.startsWith('/api/backend/api/v1/projects/p/workbench'))).toBe(true);
});
it('401 retry shares the original deadline rather than starting another full timeout', async () => {
  const fetch = vi.fn().mockResolvedValueOnce({ status: 401, body: { cancel: vi.fn() } }); vi.stubGlobal('fetch', fetch);
  auth.refreshSession.mockImplementation(() => new Promise(resolve => setTimeout(() => resolve({ data: { session } }), 150)));
  const { apiRequest } = await import('@/lib/apiClient');
  const assertion = expect(apiRequest('/api/v1/test', { timeoutMs: 100 })).rejects.toMatchObject({ name: 'TimeoutError' });
  await vi.advanceTimersByTimeAsync(100); await assertion;
  await vi.advanceTimersByTimeAsync(100); expect(fetch).toHaveBeenCalledTimes(1);
});
it('aborting one caller does not cancel shared authentication for another caller', async () => {
  let resolve!: (value: unknown) => void;
  auth.getSession.mockReturnValue(new Promise(r => { resolve = r; }));
  const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ code: 0, data: 'ok' }) }); vi.stubGlobal('fetch', fetch);
  const { apiRequest } = await import('@/lib/apiClient');
  const caller = new AbortController();
  const a = apiRequest('/api/v1/a', { signal: caller.signal }); const b = apiRequest('/api/v1/b');
  const assertion = expect(a).rejects.toMatchObject({ name: 'AbortError' }); caller.abort(); await assertion;
  resolve({ data: { session } }); expect(await b).toBe('ok'); expect(fetch).toHaveBeenCalledTimes(1);
});

it('refreshes authentication for an SSE request through the shared proxy', async () => {
  const stream = new Response('id: 1\nevent: text\ndata: {}\n\n', { headers: { 'Content-Type': 'text/event-stream' } });
  const fetch = vi.fn().mockResolvedValueOnce(new Response('{}', { status: 401 })).mockResolvedValueOnce(stream);
  vi.stubGlobal('fetch', fetch);
  auth.refreshSession.mockResolvedValue({ data: { session: { ...session, access_token: 'refreshed' } } });
  const { apiStreamRequest } = await import('@/lib/apiClient');
  const response = await apiStreamRequest('/api/v1/agents/runs/run-1/events?after=9');
  expect(await response.text()).toContain('event: text');
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(fetch.mock.calls[1][0]).toBe('/api/backend/api/v1/agents/runs/run-1/events?after=9');
  expect(fetch.mock.calls[1][1].headers.Authorization).toBe('Bearer refreshed');
});
it('keeps the SSE deadline through the body and cancels upstream on timeout', async () => {
  const cancel = vi.fn();
  const response = new Response(new ReadableStream({ pull() { return new Promise(() => {}); }, cancel }),
    { headers: { 'Content-Type': 'text/event-stream' } });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
  const { apiStreamRequest } = await import('@/lib/apiClient');
  const result = await apiStreamRequest('/api/v1/agents/runs/run-1/events', { timeoutMs: 100 });
  const reading = expect(result.text()).rejects.toMatchObject({ name: 'TimeoutError' });
  await vi.advanceTimersByTimeAsync(100); await reading; expect(cancel).toHaveBeenCalled();
});
it('propagates caller cancellation to SSE without another stop command', async () => {
  const cancel = vi.fn();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream({ pull() { return new Promise(() => {}); }, cancel }))));
  const { apiStreamRequest } = await import('@/lib/apiClient');
  const caller = new AbortController();
  const response = await apiStreamRequest('/api/v1/agents/runs/run-1/events', { signal: caller.signal });
  const reading = expect(response.text()).rejects.toMatchObject({ name: 'AbortError' });
  caller.abort(); await reading; expect(cancel).toHaveBeenCalled();
});
