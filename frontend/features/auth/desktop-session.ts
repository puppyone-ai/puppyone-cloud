import type { Session } from '@supabase/supabase-js';
import type { DesktopLoginIntent } from './login-intent';
import { desktopCallbackUrl } from './supabase/desktop-callback';

/** Top-level navigation avoids a cross-origin localhost fetch or token URL. */
export function completeDesktopSession(intent: DesktopLoginIntent, session: Session, clear: () => void): Promise<never> {
  const target = desktopCallbackUrl(intent.callbackUrl);
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
  // The browser leaves the original login page and Main renders its existing
  // result screen. Do not issue a second navigation or complete twice.
  return new Promise<never>(() => {});
}
