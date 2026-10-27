import type { Session } from '@supabase/supabase-js';
import type { DelegatedLoginIntent } from './login-intent';
import { delegatedCallbackUrl } from './delegated-client';

/**
 * Hand an isolated email session to its delegated client (Desktop loopback or
 * the PuppyOne website). Top-level form navigation avoids a cross-origin fetch
 * or a token URL; the receiver validates Origin and state, then rotates the
 * refresh token directly with Supabase.
 */
export function completeDelegatedSession(
  intent: DelegatedLoginIntent,
  session: Session,
  clear: () => void
): Promise<never> {
  const target = delegatedCallbackUrl(intent.kind, intent.callbackUrl);
  target.pathname = '/auth/session';
  if (!session.refresh_token) throw new Error('Sign-in returned no refresh credential.');
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = target.toString();
  form.hidden = true;
  for (const [name, value] of Object.entries({ state: intent.state, refresh_token: session.refresh_token })) {
    const input = document.createElement('input');
    input.type = 'hidden'; input.name = name; input.value = value;
    form.append(input);
  }
  document.body.append(form);
  clear();
  form.submit();
  // The browser leaves the original login page and the receiver renders its
  // own result. Do not issue a second navigation or complete twice.
  return new Promise<never>(() => {});
}
