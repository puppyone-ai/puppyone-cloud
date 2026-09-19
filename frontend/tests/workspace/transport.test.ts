import { afterEach, expect, it, vi } from 'vitest';
import { createRequestScope, withAbort } from '@/lib/requestScope';
import { scopedResponseBody } from '@/lib/responseStream';
import { forwardBackendRequestHeaders } from '@/lib/backendProxyHeaders';

afterEach(() => { vi.useRealTimers(); });
it('an external signal does not disable the deadline', async () => {
  vi.useFakeTimers();
  const caller = new AbortController();
  const scope = createRequestScope(caller.signal, 100);
  const result = withAbort(new Promise(() => {}), scope.signal);
  const assertion = expect(result).rejects.toMatchObject({ name: 'TimeoutError' });
  await vi.advanceTimersByTimeAsync(100); await assertion;
  expect(caller.signal.aborted).toBe(false); scope.dispose();
});
it('caller cancellation retains AbortError rather than being labelled a timeout', async () => {
  const caller = new AbortController(), scope = createRequestScope(caller.signal, 1000);
  const pending = withAbort(new Promise(() => {}), scope.signal);
  caller.abort(); await expect(pending).rejects.toMatchObject({ name: 'AbortError' }); scope.dispose();
});
it('disposing removes the timer and the caller listener', () => {
  vi.useFakeTimers(); const caller = new AbortController(), scope = createRequestScope(caller.signal, 100);
  scope.dispose(); caller.abort(); vi.advanceTimersByTime(200); expect(scope.signal.aborted).toBe(false);
});
it('deadline covers a stalled response body and cancels its reader', async () => {
  vi.useFakeTimers(); const cancel = vi.fn(); const scope = createRequestScope(null, 100);
  const reader = scopedResponseBody(new ReadableStream({ cancel }), scope).getReader();
  const assertion = expect(reader.read()).rejects.toMatchObject({ name: 'TimeoutError' });
  await vi.advanceTimersByTimeAsync(100); await assertion; expect(cancel).toHaveBeenCalledOnce();
});
it('a completed response releases the deadline', async () => {
  vi.useFakeTimers(); const scope = createRequestScope(null, 100);
  const stream = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(new Uint8Array([1])); c.close(); } });
  const reader = scopedResponseBody(stream, scope).getReader();
  expect((await reader.read()).value).toEqual(new Uint8Array([1])); expect((await reader.read()).done).toBe(true);
  vi.advanceTimersByTime(200); expect(scope.signal.aborted).toBe(false);
});
it('forwards request correlation and rejects unbounded request IDs', () => {
  const headers = forwardBackendRequestHeaders(new Headers({ 'x-request-id': 'request-123', traceparent: 'trace', 'x-private': 'ignored' }));
  expect(headers.get('x-request-id')).toBe('request-123'); expect(headers.get('traceparent')).toBe('trace'); expect(headers.has('x-private')).toBe(false);
  expect(forwardBackendRequestHeaders(new Headers({ 'x-request-id': 'x'.repeat(129) })).has('x-request-id')).toBe(false);
});
it('forwards idempotency keys required by safe create operations', () => {
  const headers = forwardBackendRequestHeaders(new Headers({
    'Idempotency-Key': '1aa4f40c-b10f-4ddd-98bb-d4762b14fe09',
  }));
  expect(headers.get('idempotency-key')).toBe('1aa4f40c-b10f-4ddd-98bb-d4762b14fe09');
});
