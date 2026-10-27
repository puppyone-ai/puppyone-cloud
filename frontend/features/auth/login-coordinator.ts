import type { Session, SupabaseClient } from '@supabase/supabase-js';
import { isDelegatedIntent, type LoginIntent } from './login-intent';
import { completeDelegatedSession } from './delegated-session';
import { authEvent } from './auth-errors';

export async function completeWebLogin(
  session: Session,
  next: string | null,
  apiUrl: string,
  fetcher: typeof fetch = fetch
): Promise<string> {
  let project: string | null = null;
  try {
    const response = await fetcher(`${apiUrl}/auth/initialize`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${session.access_token}` },
      cache: 'no-store',
      signal: AbortSignal.timeout(10_000),
    });
    if (response.ok) {
      const id = (await response.json())?.data?.demo_project_id;
      if (typeof id === 'string' && /^[a-zA-Z0-9_-]+$/.test(id)) project = id;
    }
  } catch {
    /* Identity remains valid; business initialization is retryable. */
  }
  return next ?? (project ? `/projects/${project}/data` : '/home');
}

export function createLoginCoordinator({
  client,
  intent,
  clear,
  fetcher = fetch,
}: {
  client: SupabaseClient;
  intent: LoginIntent;
  clear?: () => void;
  fetcher?: typeof fetch;
}) {
  let completion: Promise<string> | null = null;
  return {
    finish(): Promise<string> {
      if (completion) return completion;
      completion = (async () => {
        const { data, error } = await client.auth.getSession();
        if (error || !data.session)
          throw new Error('Sign-in succeeded but no session was returned.');
        authEvent('identity_verified', intent.kind);
        if (isDelegatedIntent(intent)) {
          if (!clear) throw new Error('Delegated sign-in context is unavailable.');
          return completeDelegatedSession(intent, data.session, clear);
        }
        return completeWebLogin(data.session, intent.next, '/api/backend/api/v1', fetcher);
      })();
      // A failed attempt remains visible; a deliberate retry can run again.
      completion.catch(() => {
        completion = null;
      });
      return completion;
    },
  };
}
