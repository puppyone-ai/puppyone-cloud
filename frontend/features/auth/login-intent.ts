import { safeNext } from '@/lib/safeNext';
export { safeNext } from '@/lib/safeNext';
import { desktopCallbackUrl } from './supabase/desktop-callback';
export type SignInProvider = 'email' | 'google' | 'github';
export const DESKTOP_ATTEMPT_TTL_MS = 10 * 60 * 1000;
export type DesktopLoginIntent = { kind: 'desktop'; state: string; callbackUrl: string; challenge: string };
export type LoginIntent = { kind: 'web'; next: string | null } | DesktopLoginIntent;

export function readLoginIntent(url: URL): LoginIntent {
  if (url.searchParams.get('client') === 'desktop') {
    const state = url.searchParams.get('desktop_state') ?? '';
    const challenge = url.searchParams.get('code_challenge') ?? '';
    if (!/^[A-Za-z0-9_-]{43,128}$/.test(state) || !/^[A-Za-z0-9_-]{43}$/.test(challenge)
      || ['desktop_state', 'desktop_callback', 'code_challenge'].some(key => url.searchParams.getAll(key).length !== 1))
      throw new Error('Sign-in request is invalid or expired. Start sign-in again from Desktop.');
    return { kind: 'desktop', state, challenge, callbackUrl: desktopCallbackUrl(url.searchParams.get('desktop_callback') ?? '').toString() };
  }
  return { kind: 'web', next: safeNext(url.searchParams.get('redirect') ?? url.searchParams.get('next')) };
}

export function loginReturnPath(intent: LoginIntent): string {
  if (intent.kind === 'desktop') return '/login?' + new URLSearchParams({ client: 'desktop', desktop_state: intent.state, desktop_callback: intent.callbackUrl, code_challenge: intent.challenge });
  return '/login' + (intent.next ? `?${new URLSearchParams({ redirect: intent.next })}` : '');
}

export function oauthCallbackUrl(base: string, intent: LoginIntent): string {
  const url = new URL(base);
  if (intent.kind === 'desktop') {
    url.searchParams.set('desktop_state', intent.state);
    url.searchParams.set('desktop_callback', intent.callbackUrl);
  } else if (intent.next) url.searchParams.set('next', intent.next);
  return url.toString();
}
