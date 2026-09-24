import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import { DESKTOP_ATTEMPT_TTL_MS } from '../login-intent';

const PREFIX = 'puppyone:desktop-auth:';
type Attempt = { createdAt: number; proof: string };

export function createDesktopContext({
  url,
  key,
  state,
  storage,
  now = Date.now(),
  returning = false,
}: {
  url: string;
  key: string;
  state: string;
  storage: Storage;
  now?: number;
  returning?: boolean;
}) {
  const namespace = `${PREFIX}${state}`;
  const attemptKey = `${namespace}:attempt`;
  const authKey = `${namespace}:session`;
  let attempt: Attempt | null = null;
  try {
    attempt = JSON.parse(storage.getItem(attemptKey) ?? 'null');
  } catch {
    /* reject or replace below */
  }
  const valid =
    attempt &&
    Number.isFinite(attempt.createdAt) &&
    now >= attempt.createdAt &&
    now - attempt.createdAt < DESKTOP_ATTEMPT_TTL_MS &&
    /^[a-f0-9]{64}$/.test(attempt.proof);
  if (!valid) {
    if (returning || attempt) {
      clearTemporaryStorage(storage, namespace);
      throw new Error('Sign-in request expired. Start sign-in again from Desktop.');
    }
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    attempt = {
      createdAt: now,
      proof: Array.from(bytes, b => b.toString(16).padStart(2, '0')).join(''),
    };
    storage.setItem(attemptKey, JSON.stringify(attempt));
  }
  const client = createClient(url, key, {
    auth: {
      flowType: 'pkce',
      persistSession: true,
      autoRefreshToken: false,
      detectSessionInUrl: false,
      storage,
      storageKey: authKey,
    },
  });
  return {
    client,
    proof: attempt!.proof,
    clear: () => clearDesktopContext(client, storage, namespace),
  };
}

export function clearDesktopContext(client: SupabaseClient, storage: Storage, namespace: string) {
  client.auth.stopAutoRefresh();
  // Clear only this temporary context. signOut() would revoke the session
  // already transferred to Main, while Web cookies must remain untouched.
  clearTemporaryStorage(storage, namespace);
}

function clearTemporaryStorage(storage: Storage, namespace: string) {
  const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index));
  for (const key of keys) if (key?.startsWith(`${namespace}:`)) storage.removeItem(key);
}
