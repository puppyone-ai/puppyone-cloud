import { createBrowserClient } from '@supabase/ssr';
import type { SupabaseClient } from '@supabase/supabase-js';
import { getOAuthCallbackUrl } from '@/lib/auth-urls';
import { authEvent, backendAuthError } from '../auth-errors';
import { bindDesktopBrowser } from '../desktop-handoff';
import { createLoginCoordinator } from '../login-coordinator';
import { oauthCallbackUrl, readLoginIntent, type SignInProvider } from '../login-intent';
import { createDesktopContext } from './desktop-context';

export function createBrowserLogin(location: URL, storage: Storage, fetcher: typeof fetch = fetch) {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) throw new Error('Supabase is not configured');
  const intent = readLoginIntent(location);
  const temporary =
    intent.kind === 'desktop'
      ? createDesktopContext({
          url,
          key,
          state: intent.state,
          storage,
          returning: location.searchParams.has('auth_code') || location.searchParams.has('error'),
        })
      : null;
  // @supabase/ssr 0.5 predates the current supabase-js schema generics. Both
  // factories return the same runtime client; normalize only this boundary.
  const client = temporary?.client ?? (createBrowserClient(url, key) as unknown as SupabaseClient);
  const coordinator = createLoginCoordinator({
    client,
    intent,
    proof: temporary?.proof,
    clear: temporary?.clear,
    fetcher,
  });
  let enabled: SignInProvider[] = ['email'];
  let initialization: Promise<SignInProvider[]> | null = null;
  const code = location.searchParams.get('auth_code');
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
    if (intent.kind === 'desktop' && temporary) {
      await bindDesktopBrowser(intent.state, temporary.proof, fetcher);
      if (code) {
        const result = await client.auth.exchangeCodeForSession(code);
        if (result.error || !result.data.session)
          throw new Error('Sign-in failed. Start sign-in again from Desktop.');
        authEvent('provider_returned', 'desktop');
      }
    }
    return enabled;
  }
}
