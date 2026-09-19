/** One deadline for auth, fetch, retry and body consumption. Caller cancellation
 * never cancels shared token refreshes, but stops this caller waiting for them. */
export function createRequestScope(signal?: AbortSignal | null, timeoutMs = 30_000) {
  const controller = new AbortController();
  const cancel = () => controller.abort(signal?.reason ?? new DOMException('Request cancelled', 'AbortError'));
  if (signal?.aborted) cancel();
  else signal?.addEventListener('abort', cancel, { once: true });
  const timer = timeoutMs > 0 ? setTimeout(() => {
    controller.abort(new DOMException('Request deadline exceeded', 'TimeoutError'));
  }, timeoutMs) : undefined;
  return {
    signal: controller.signal,
    abort: (reason?: unknown) => controller.abort(reason),
    dispose: () => { clearTimeout(timer); signal?.removeEventListener('abort', cancel); },
  };
}

export function withAbort<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason ?? new DOMException('Request cancelled', 'AbortError'));
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, { once: true });
    // Always attach both handlers: a shared operation can reject after this
    // caller leaves, and must not become an unhandled rejection.
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}
