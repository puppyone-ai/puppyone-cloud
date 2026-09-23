export const AUTH_ERROR_MESSAGES: Record<string, string> = {
  auth_callback_failed: 'Sign-in failed. Please try again.',
  auth_cancelled: 'Sign-in was cancelled. You can try again.',
  auth_request_expired: 'Sign-in request expired. Start sign-in again from Desktop.',
};

export function backendAuthError(payload: unknown, fallback: string): Error {
  const value = payload as { message?: unknown; detail?: { message?: unknown } | string } | null;
  const detail = typeof value?.detail === 'string' ? value.detail : value?.detail?.message;
  const message =
    typeof detail === 'string'
      ? detail
      : typeof value?.message === 'string'
        ? value.message
        : fallback;
  return new Error(message);
}

export function authEvent(stage: string, intent: 'web' | 'desktop', provider?: string) {
  // Only bounded, credential-free metadata. Never pass SDK sessions or URLs.
  if (typeof window !== 'undefined')
    window.dispatchEvent(
      new CustomEvent('puppyone:auth-event', { detail: { stage, intent, provider } })
    );
}
