export type SignInProvider = 'email' | 'google' | 'github';
export type LoginIntent = { kind: 'web'; next: string | null };

export function safeNext(value: string | null | undefined): string | null {
  if (
    !value ||
    !value.startsWith('/') ||
    value.startsWith('//') ||
    /[\\\u0000-\u0020]/.test(value) ||
    /%(?:5c|0[0-9a-f]|1[0-9a-f])/i.test(value)
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
  if (url.searchParams.get('client') === 'desktop')
    throw new Error('This Desktop sign-in link is no longer supported. Update Puppyone and start sign-in again.');
  return { kind: 'web', next: safeNext(url.searchParams.get('redirect') ?? url.searchParams.get('next')) };
}

export function loginReturnPath(intent: LoginIntent): string {
  return '/login' + (intent.next ? `?${new URLSearchParams({ redirect: intent.next })}` : '');
}

export function oauthCallbackUrl(base: string, intent: LoginIntent): string {
  const url = new URL(base);
  if (intent.next) url.searchParams.set('next', intent.next);
  return url.toString();
}
