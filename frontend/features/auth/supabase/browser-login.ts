import { createBrowserClient } from '@supabase/ssr';
import type { SupabaseClient } from '@supabase/supabase-js';
import { getOAuthCallbackUrl } from '@/lib/auth-urls';
import { authEvent, backendAuthError } from '../auth-errors';
import { createLoginCoordinator } from '../login-coordinator';
import { oauthCallbackUrl, readLoginIntent, type SignInProvider } from '../login-intent';

export function createBrowserLogin(location: URL, _storage: Storage, fetcher: typeof fetch = fetch) {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) throw new Error('Supabase is not configured');
  const intent = readLoginIntent(location);
  const client = createBrowserClient(url, key) as unknown as SupabaseClient;
  const coordinator = createLoginCoordinator({
    client,
    intent,
    fetcher,
  });
  let enabled: SignInProvider[] = ['email'];
  let initialization: Promise<SignInProvider[]> | null = null;
  return {
    client,
    intent,
    finish: coordinator.finish,
    initialize(): Promise<SignInProvider[]> {
      initialization ??= initialize();
      return initialization;
    },
    async signInWithProvider(provider: 'google' | 'github') {
      if (!enabled.includes(provider))
        throw new Error('This sign-in method is unavailable in this environment.');
      authEvent('login_started', intent.kind, provider);
      const { error } = await client.auth.signInWithOAuth({
        provider,
        options: { redirectTo: oauthCallbackUrl(getOAuthCallbackUrl(), intent) },
      });
      if (error) throw error;
    },
  };
  async function initialize(): Promise<SignInProvider[]> {
    if (location.pathname !== '/login') return enabled;
    const response = await fetcher('/api/backend/api/v1/auth/methods', {
      cache: 'no-store',
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok)
      throw backendAuthError(
        await response.json().catch(() => null),
        'Sign-in methods are temporarily unavailable.'
      );
    const providers = (await response.json())?.data?.providers;
    enabled = ['email', 'google', 'github'].filter(
      name =>
        Array.isArray(providers) &&
        providers.includes(name) &&
        (process.env.NEXT_PUBLIC_AUTH_EMAIL_ONLY !== 'true' || name === 'email')
    ) as SignInProvider[];
    return enabled;
  }
}
