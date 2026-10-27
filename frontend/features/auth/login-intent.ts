import { safeNext } from '@/lib/safeNext';
export { safeNext } from '@/lib/safeNext';
import {
  delegatedCallbackUrl,
  isDelegatedClient,
  restartSignInMessage,
  type DelegatedClient,
} from './delegated-client';
export type SignInProvider = 'email' | 'google' | 'github';
export const DESKTOP_ATTEMPT_TTL_MS = 10 * 60 * 1000;
/** A client that reuses this login page while owning its own verifier and session. */
export type DelegatedLoginIntent = {
  kind: DelegatedClient;
  state: string;
  callbackUrl: string;
  challenge: string;
};
export type DesktopLoginIntent = DelegatedLoginIntent & { kind: 'desktop' };
export type WebsiteLoginIntent = DelegatedLoginIntent & { kind: 'website' };
export type LoginIntent = { kind: 'web'; next: string | null } | DelegatedLoginIntent;

export function isDelegatedIntent(
  intent: LoginIntent | null | undefined
): intent is DelegatedLoginIntent {
  return Boolean(intent) && intent!.kind !== 'web';
}

export function readLoginIntent(url: URL): LoginIntent {
  const client = url.searchParams.get('client');
  if (isDelegatedClient(client)) {
    const stateKey = `${client}_state`;
    const callbackKey = `${client}_callback`;
    const state = url.searchParams.get(stateKey) ?? '';
    const challenge = url.searchParams.get('code_challenge') ?? '';
    if (!/^[A-Za-z0-9_-]{43,128}$/.test(state) || !/^[A-Za-z0-9_-]{43}$/.test(challenge)
      || [stateKey, callbackKey, 'code_challenge'].some(key => url.searchParams.getAll(key).length !== 1))
      throw new Error(restartSignInMessage(client));
    let callbackUrl: string;
    try {
      callbackUrl = delegatedCallbackUrl(client, url.searchParams.get(callbackKey) ?? '').toString();
    } catch {
      throw new Error(restartSignInMessage(client));
    }
    return { kind: client, state, challenge, callbackUrl };
  }
  return { kind: 'web', next: safeNext(url.searchParams.get('redirect') ?? url.searchParams.get('next')) };
}

export function loginReturnPath(intent: LoginIntent): string {
  if (isDelegatedIntent(intent))
    return '/login?' + new URLSearchParams({
      client: intent.kind,
      [`${intent.kind}_state`]: intent.state,
      [`${intent.kind}_callback`]: intent.callbackUrl,
      code_challenge: intent.challenge,
    });
  return '/login' + (intent.next ? `?${new URLSearchParams({ redirect: intent.next })}` : '');
}

export function oauthCallbackUrl(base: string, intent: LoginIntent): string {
  const url = new URL(base);
  if (isDelegatedIntent(intent)) {
    url.searchParams.set(`${intent.kind}_state`, intent.state);
    url.searchParams.set(`${intent.kind}_callback`, intent.callbackUrl);
  } else if (intent.next) url.searchParams.set('next', intent.next);
  return url.toString();
}
