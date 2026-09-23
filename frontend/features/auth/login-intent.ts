export type SignInProvider = 'email' | 'google' | 'github';
export type LoginIntent = { kind: 'web'; next: string | null } | { kind: 'desktop'; state: string };
export const DESKTOP_STATE = /^[A-Za-z0-9_-]{32,128}$/;
export const DESKTOP_ATTEMPT_TTL_MS = 10 * 60 * 1000;

export function safeNext(value: string | null | undefined): string | null {
  if (
    !value ||
    !value.startsWith('/') ||
    value.startsWith('//') ||
    /[\\\u0000-\u0020]/.test(value) ||
    /%(?:5c|0[0-9a-f]|1[0-9a-f]|20)/i.test(value)
  )
    return null;
  const parsed = new URL(value, 'https://return.invalid');
  if (
    parsed.origin !== 'https://return.invalid' ||
    /^\/(?:login|auth)(?:\/|$)/.test(parsed.pathname)
  )
    return null;
  return value;
}

export function readLoginIntent(url: URL): LoginIntent {
  if (url.searchParams.get('client') !== 'desktop')
    return {
      kind: 'web',
      next: safeNext(url.searchParams.get('redirect') ?? url.searchParams.get('next')),
    };
  const state = url.searchParams.get('desktop_state') ?? url.searchParams.get('state') ?? '';
  if (!DESKTOP_STATE.test(state))
    throw new Error('Sign-in request is invalid or expired. Start sign-in again.');
  return { kind: 'desktop', state };
}

export function loginReturnPath(intent: LoginIntent): string {
  const query = new URLSearchParams();
  if (intent.kind === 'desktop') {
    query.set('client', 'desktop');
    query.set('desktop_state', intent.state);
  } else if (intent.next) query.set('redirect', intent.next);
  return '/login' + (query.size ? `?${query}` : '');
}

export function oauthCallbackUrl(base: string, intent: LoginIntent): string {
  const url = new URL(base);
  if (intent.kind === 'desktop') url.searchParams.set('desktop_state', intent.state);
  else if (intent.next) url.searchParams.set('next', intent.next);
  return url.toString();
}
