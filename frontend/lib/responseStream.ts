import { withAbort, type createRequestScope } from './requestScope';

/** Keep the upstream deadline alive until the streamed body ends, not merely
 * until headers arrive. Cancellation must reach upstream and release locks. */
export function scopedResponseBody(body: ReadableStream<Uint8Array>, scope: ReturnType<typeof createRequestScope>) {
  const reader = body.getReader();
  let finished = false;
  const finish = () => { if (!finished) { finished = true; scope.dispose(); } };
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { done, value } = await withAbort(reader.read(), scope.signal);
        if (done) { finish(); reader.releaseLock(); controller.close(); }
        else controller.enqueue(value);
      } catch (error) {
        finish();
        void reader.cancel(error).catch(() => {}).finally(() => reader.releaseLock());
        controller.error(error);
      }
    },
    async cancel(reason) {
      scope.abort(reason);
      finish();
      try { await reader.cancel(reason); } finally { reader.releaseLock(); }
    },
  });
}
