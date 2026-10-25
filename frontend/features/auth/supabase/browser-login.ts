import { createDesktopContext } from './desktop-context';
import { createBrowserClient } from '@supabase/ssr';
import type { SupabaseClient } from '@supabase/supabase-js';
import { getOAuthCallbackUrl } from '@/lib/auth-urls';
import { authEvent } from '../auth-errors';
import { createLoginCoordinator } from '../login-coordinator';
import { oauthCallbackUrl, readLoginIntent, type SignInProvider } from '../login-intent';

export function createBrowserLogin(location: URL, storage: Storage, fetcher: typeof fetch = fetch) {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) throw new Error('Supabase is not configured');
  const intent = readLoginIntent(location);
  const temporary = intent.kind === 'desktop' ? createDesktopContext({ url, key, state: intent.state, storage }) : null;
  const client = temporary?.client ?? (createBrowserClient(url, key) as unknown as SupabaseClient);
  const coordinator = createLoginCoordinator({
    client,
    intent,
    clear: temporary?.clear,
    fetcher,
  });
  let enabled: SignInProvider[] = ['email'];
  let initialization: Promise<SignInProvider[]> | null = null;
  let providerDiscovery: Promise<SignInProvider[]> | null = null;
  return {
    client,
    intent,
    finish: coordinator.finish,
    initialize(): Promise<SignInProvider[]> {
      initialization ??= initialize();
      return initialization;
    },
    discoverProviders(): Promise<SignInProvider[]> {
      providerDiscovery ??= discoverProviders();
      return providerDiscovery;
    },
    async signInWithProvider(provider: 'google' | 'github') {
      if (!enabled.includes(provider))
        throw new Error('This sign-in method is unavailable in this environment.');
      authEvent('login_started', intent.kind, provider);
      if (intent.kind === 'desktop') {
        const authorize = new URL('/auth/v1/authorize', url);
        authorize.search = new URLSearchParams({ provider,
          redirect_to: oauthCallbackUrl(getOAuthCallbackUrl(), intent),
          code_challenge: intent.challenge, code_challenge_method: 's256',
        }).toString();
        window.location.assign(authorize.toString());
        return;
      }
      const { error } = await client.auth.signInWithOAuth({
        provider,
        options: { redirectTo: oauthCallbackUrl(getOAuthCallbackUrl(), intent) },
      });
      if (error) throw error;
    },
  };
  async function initialize(): Promise<SignInProvider[]> {
    // Email is the baseline sign-in path and never waits on the network.
    return enabled;
  }

  async function discoverProviders(): Promise<SignInProvider[]> {
    // Optional OAuth buttons are discovered after the form is usable, so a slow
    // or failing capability probe cannot block email sign-in.
    if (location.pathname !== '/login' || process.env.NEXT_PUBLIC_AUTH_EMAIL_ONLY === 'true')
      return enabled;
    try {
      const response = await fetcher('/api/backend/api/v1/auth/methods', {
        cache: 'no-store',
        signal: AbortSignal.timeout(10_000),
      });
      if (response.ok) {
        const providers = (await response.json())?.data?.providers;
        if (Array.isArray(providers))
          enabled = ['email', ...(['google', 'github'] as const).filter(name => providers.includes(name))];
      }
    } catch {
      // Advisory only: the actual sign-in request reports real failures.
    }
    return enabled;
  }
}
