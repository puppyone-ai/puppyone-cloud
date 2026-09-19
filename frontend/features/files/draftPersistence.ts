/** Buffered local persistence, never a server autosave. The first edit starts
 * a fixed window, so continuous typing still flushes at least every 250 ms. */
export function createDraftWriter<T>(write: (value: T) => void, delayMs = 250) {
  let pending: { value: T } | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const cancel = () => { clearTimeout(timer); timer = undefined; pending = undefined; };
  const flush = () => { const next = pending; cancel(); if (next) write(next.value); };
  return {
    schedule(value: T) { pending = { value }; timer ??= setTimeout(flush, delayMs); },
    flush,
    cancel,
  };
}
