import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { safeNext, readLoginIntent } from '@/features/auth/login-intent';
import { createLoginCoordinator } from '@/features/auth/login-coordinator';
import { desktopCallbackTarget } from '@/features/auth/supabase/desktop-callback';

const mocks = vi.hoisted(() => ({ server: vi.fn(), exchange: vi.fn(), setCookie: vi.fn() }));
vi.mock('@supabase/ssr', () => ({ createServerClient: mocks.server }));
vi.mock('next/headers', () => ({
  cookies: async () => ({ getAll: () => [], set: mocks.setCookie }),
}));
vi.mock('@/lib/server-env', () => ({
  getRequestOrigin: () => 'https://web.example.test',
  getServerSupabaseUrl: () => 'https://auth.example.test',
  getPublicSupabaseUrl: () => 'https://auth.example.test',
  getSupabaseAnonKey: () => 'public-key',
  getServerApiBaseUrl: () => 'https://api.example.test',
}));
import { GET } from '@/app/auth/callback/route';
import { middleware } from '@/middleware';
import { NextRequest } from 'next/server';
const state = 'a'.repeat(43);
const session = { access_token: 'access', refresh_token: 'refresh', user: { id: 'user' } } as any;
beforeEach(() => {
  mocks.server.mockReturnValue({ auth: { exchangeCodeForSession: mocks.exchange } });
});
afterEach(() => vi.unstubAllGlobals());

it.each([
  '//evil.test',
  '/\\evil.test',
  '/%5cevil.test',
  '/login',
  '/auth/callback',
  '/\nevil.test',
])('rejects unsafe return path %s', value => expect(safeNext(value)).toBeNull());
it('preserves valid app return path and rejects incomplete Desktop intent', () => {
  expect(safeNext('/invite/abc?x=1')).toBe('/invite/abc?x=1');
  expect(() => readLoginIntent(new URL('https://web.test/login?client=desktop'))).toThrow();
});

it('Desktop OAuth callback preserves intent without using Web cookie session', async () => {
  const response = await GET(
    new Request(`https://web.example.test/auth/callback?desktop_state=${state}&desktop_callback=http%3A%2F%2F127.0.0.1%3A4000%2Fauth%2Fcallback&code=provider-code`)
  );
  const target = new URL(response.headers.get('location')!);
  expect(target.origin).toBe('http://127.0.0.1:4000');
  expect(target.pathname).toBe('/auth/callback');
  expect(target.searchParams.get('code')).toBe('provider-code');
  expect(target.searchParams.get('state')).toBe(state);
  expect(mocks.server).not.toHaveBeenCalled();
  expect(response.headers.get('cache-control')).toContain('no-store');
  expect(response.headers.get('referrer-policy')).toBe('no-referrer');
});

it('provider cancellation preserves the Desktop attempt', async () => {
  const response = await GET(
    new Request(
      `https://web.example.test/auth/callback?desktop_state=${state}&desktop_callback=http%3A%2F%2F127.0.0.1%3A4000%2Fauth%2Fcallback&error=access_denied&error_description=private`
    )
  );
  const target = new URL(response.headers.get('location')!);
  expect(target.searchParams.get('state')).toBe(state);
  expect(target.searchParams.get('error')).toBe('access_denied');
  expect(target.search).not.toContain('private');
});

it('Desktop login middleware never touches the existing Web session', async () => {
  const response = await middleware(
    new NextRequest(`https://web.example.test/login?client=desktop&desktop_state=${state}`, {
      headers: { cookie: 'web-session=existing' },
    })
  );
  expect(response.status).toBe(200);
  expect(mocks.server).not.toHaveBeenCalled();
  expect(response.headers.get('set-cookie')).toBeNull();
});

it('Web OAuth callback exchanges once, initializes and returns to invite', async () => {
  mocks.exchange.mockResolvedValue({ data: { session }, error: null });
  const fetcher = vi.fn(async () => Response.json({ data: { demo_project_id: 'demo' } }));
  vi.stubGlobal('fetch', fetcher);
  const response = await GET(
    new Request('https://web.example.test/auth/callback?code=code&next=%2Finvite%2Fabc')
  );
  expect(response.headers.get('location')).toBe('https://web.example.test/invite/abc');
  expect(mocks.exchange).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls).toHaveLength(1);
});

it('parallel Web completion initializes once without any Desktop handoff', async () => {
  const fetcher = vi.fn(async (_url: RequestInfo | URL) => Response.json({ data: {} }));
  const coordinator = createLoginCoordinator({
    client: { auth: { getSession: async () => ({ data: { session } }) } } as any,
    intent: { kind: 'web', next: '/invite/one' }, fetcher,
  });
  expect(await Promise.all([coordinator.finish(), coordinator.finish(), coordinator.finish()])).toEqual(['/invite/one', '/invite/one', '/invite/one']);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][0]).toBe('/api/backend/api/v1/auth/initialize');
});

it.each([
  'https://evil.test/auth/callback', 'http://localhost:4000/auth/callback',
  'http://127.0.0.1:4000/wrong', 'http://127.0.0.1:4000/auth/callback?code=injected',
  'http://user@127.0.0.1:4000/auth/callback', 'http://127.0.0.1:80/auth/callback',
  'puppyone://auth/callback', 'http://127.0.0.1.evil.test:4000/auth/callback',
])('rejects unsafe callback destination %s', target => {
  const url = new URL('https://web.test/auth/callback');
  url.search = new URLSearchParams({ desktop_state: state, desktop_callback: target, code: 'code' }).toString();
  expect(() => desktopCallbackTarget(url)).toThrow();
});

it('rejects ambiguous state and omits injected credentials from the relay', () => {
  const url = new URL(`https://web.test/auth/callback?desktop_state=${state}&desktop_callback=http%3A%2F%2F127.0.0.1%3A4000%2Fauth%2Fcallback&code=code&access_token=secret`);
  expect(desktopCallbackTarget(url)).not.toContain('secret');
  url.searchParams.append('desktop_state', state);
  expect(() => desktopCallbackTarget(url)).toThrow();
});
