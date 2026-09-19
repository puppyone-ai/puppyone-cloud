/** Missing data is not an empty result. Background refresh keeps this identity's
 * last successful snapshot; callers render errors separately from empty states. */
export function readState<T>(enabled: boolean, data: T | undefined, error: unknown) {
  if (!enabled) return 'idle' as const;
  if (data !== undefined) return 'ready' as const;
  return error ? 'error' as const : 'loading' as const;
}
