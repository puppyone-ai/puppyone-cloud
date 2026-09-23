import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createDesktopContext } from '@/features/auth/supabase/desktop-context';
import { createBrowserLogin } from '@/features/auth/supabase/browser-login';

const state = 'c'.repeat(43);
beforeEach(() => {
  sessionStorage.clear();
  vi.stubEnv('NEXT_PUBLIC_SUPABASE_URL', 'https://auth.example.test');
  vi.stubEnv('NEXT_PUBLIC_SUPABASE_ANON_KEY', 'public-key');
  vi.stubEnv('NEXT_PUBLIC_APP_URL', 'http://localhost:3000');
  vi.stubEnv('NEXT_PUBLIC_AUTH_EMAIL_ONLY', 'false');
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

it('actual SDK uses PKCE and isolated storage; cleanup never revokes sessions', async () => {
  sessionStorage.setItem('web-sentinel', 'preserved');
  const context = createDesktopContext({
    url: 'https://auth.example.test',
    key: 'public-key',
    state,
    storage: sessionStorage,
  });
  const result = await context.client.auth.signInWithOAuth({
    provider: 'google',
    options: {
      redirectTo: `http://localhost:3000/auth/callback?desktop_state=${state}`,
      skipBrowserRedirect: true,
    },
  });
  const authorize = new URL(result.data.url!);
  expect(authorize.pathname).toBe('/auth/v1/authorize');
  expect(authorize.searchParams.get('code_challenge_method')).toBe('s256');
  expect(authorize.searchParams.get('code_challenge')).toBeTruthy();
  const verifierKey = `puppyone:desktop-auth:${state}:session-code-verifier`;
  expect(sessionStorage.getItem(verifierKey)).toBeTruthy();
  const signOut = vi.spyOn(context.client.auth, 'signOut');
  context.clear();
  expect(signOut).not.toHaveBeenCalled();
  expect(sessionStorage.getItem(verifierKey)).toBeNull();
  expect(sessionStorage.getItem('web-sentinel')).toBe('preserved');
});

it('missing or expired attempt cannot accept an OAuth return', () => {
  expect(() =>
    createDesktopContext({
      url: 'https://auth.example.test',
      key: 'public-key',
      state,
      storage: sessionStorage,
      returning: true,
    })
  ).toThrow('expired');
  sessionStorage.setItem(
    `puppyone:desktop-auth:${state}:attempt`,
    JSON.stringify({ createdAt: 1, proof: 'a'.repeat(64) })
  );
  expect(() =>
    createDesktopContext({
      url: 'https://auth.example.test',
      key: 'public-key',
      state,
      storage: sessionStorage,
      now: 600002,
    })
  ).toThrow('expired');
});

it('actual SDK exchanges once through repeated initialization, then hands off', async () => {
  const namespace = `puppyone:desktop-auth:${state}`;
  sessionStorage.setItem(
    `${namespace}:attempt`,
    JSON.stringify({ createdAt: Date.now(), proof: 'd'.repeat(64) })
  );
  sessionStorage.setItem(`${namespace}:session-code-verifier`, JSON.stringify('sdk-verifier'));
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes('/auth/v1/token')) {
      expect(JSON.parse(String(init?.body))).toMatchObject({
        auth_code: 'provider-code',
        code_verifier: 'sdk-verifier',
      });
      return Response.json({
        access_token: 'header.payload.signature',
        refresh_token: 'fresh-refresh',
        expires_in: 3600,
        token_type: 'bearer',
        user: { id: 'user-1', aud: 'authenticated' },
      });
    }
    if (url.endsWith('/auth/methods'))
      return Response.json({ data: { providers: ['email', 'google', 'github'] } });
    if (url.endsWith('/desktop/bind')) return Response.json({ data: { bound: true } });
    if (url.endsWith('/desktop/complete'))
      return Response.json({
        data: { redirect_url: `http://127.0.0.1:4000/auth/callback?state=${state}&code=handoff` },
      });
    throw new Error('Unexpected request: ' + url);
  });
  vi.stubGlobal('fetch', fetcher);
  const login = createBrowserLogin(
    new URL(
      `http://localhost:3000/login?client=desktop&desktop_state=${state}&auth_code=provider-code`
    ),
    sessionStorage,
    fetcher
  );
  await Promise.all([login.initialize(), login.initialize()]);
  expect(fetcher.mock.calls.filter(([url]) => String(url).includes('/auth/v1/token'))).toHaveLength(
    1
  );
  await login.finish();
  expect(sessionStorage.getItem(`${namespace}:session`)).toBeNull();
});
