import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { safeNext, readLoginIntent, loginReturnPath, oauthCallbackUrl } from '@/features/auth/login-intent';
import { createLoginCoordinator } from '@/features/auth/login-coordinator';
import { desktopCallbackTarget } from '@/features/auth/supabase/desktop-callback';
import { allowedWebsiteOrigins, websiteCallbackTarget } from '@/features/auth/supabase/website-callback';

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

// Next.js rewrites the first loopback host anywhere in a server request URL to
// `localhost`. A loopback dev server absorbs it in its own host; deployed
// servers (0.0.0.0 or a private address) rewrite the Desktop callback instead.
it.each(['http://127.0.0.1:3000', 'http://0.0.0.0:8080', 'http://[fd12::1]:8080'])(
  'Desktop OAuth callback relays to 127.0.0.1 when served from %s',
  async serverOrigin => {
    const response = await GET(
      new NextRequest(
        `${serverOrigin}/auth/callback?desktop_state=${state}&desktop_callback=http%3A%2F%2F127.0.0.1%3A4000%2Fauth%2Fcallback&code=provider-code`
      )
    );
    const target = new URL(response.headers.get('location')!);
    expect(target.origin).toBe('http://127.0.0.1:4000');
    expect(target.pathname).toBe('/auth/callback');
    expect(target.searchParams.get('code')).toBe('provider-code');
    expect(target.searchParams.get('state')).toBe(state);
  }
);

it('Desktop OAuth callback still rejects non-loopback targets and logs only the reason', async () => {
  const log = vi.spyOn(console, 'error').mockImplementation(() => {});
  try {
    for (const callback of ['http://evil.test:4000/auth/callback', 'https://localhost:4000/auth/callback']) {
      const response = await GET(
        new NextRequest(
          `http://0.0.0.0:8080/auth/callback?desktop_state=${state}&desktop_callback=${encodeURIComponent(callback)}&code=provider-code`
        )
      );
      expect(new URL(response.headers.get('location')!).search).toBe('?error=auth_request_expired');
    }
    expect(log).toHaveBeenCalledWith('[auth/callback] delegated relay rejected', {
      client: 'desktop',
      reason: 'Invalid Desktop callback',
    });
    expect(JSON.stringify(log.mock.calls)).not.toContain(state);
    expect(JSON.stringify(log.mock.calls)).not.toContain('provider-code');
  } finally {
    log.mockRestore();
  }
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

it('never relays a code to localhost, only to the IPv4 loopback Desktop listens on', () => {
  const url = new URL('https://web.test/auth/callback');
  url.search = new URLSearchParams({ desktop_state: state, desktop_callback: 'http://localhost:4000/auth/callback', code: 'code' }).toString();
  expect(new URL(desktopCallbackTarget(url)).origin).toBe('http://127.0.0.1:4000');
  // The browser-side intent sees the unmodified URL and stays strict.
  expect(() => readLoginIntent(new URL(
    `https://web.test/login?client=desktop&desktop_state=${state}&code_challenge=${'c'.repeat(43)}&desktop_callback=${encodeURIComponent('http://localhost:4000/auth/callback')}`
  ))).toThrow();
});

it.each([
  'https://evil.test/auth/callback', 'https://localhost:4000/auth/callback',
  'http://localhost:80/auth/callback', 'http://127.0.0.1:4000/wrong', 'http://127.0.0.1:4000/auth/callback?code=injected',
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

describe('PuppyOne website as a delegated login client', () => {
  const website = 'https://www.example.test';
  const callback = `${website}/auth/callback`;
  const challenge = 'c'.repeat(43);
  beforeEach(() => vi.stubEnv('NEXT_PUBLIC_AUTH_WEBSITE_ORIGINS', `${website}, http://localhost:3001`));
  afterEach(() => vi.unstubAllEnvs());
  const loginUrl = (overrides: Record<string, string> = {}) => {
    const url = new URL('https://web.test/login');
    url.search = new URLSearchParams({
      client: 'website', website_state: state, website_callback: callback, code_challenge: challenge, ...overrides,
    }).toString();
    return url;
  };

  it('reads an allowlisted website intent and round-trips it through login and OAuth URLs', () => {
    const intent = readLoginIntent(loginUrl());
    expect(intent).toEqual({ kind: 'website', state, challenge, callbackUrl: callback });
    expect(readLoginIntent(new URL(`https://web.test${loginReturnPath(intent)}`))).toEqual(intent);
    const oauth = new URL(oauthCallbackUrl('https://web.test/auth/callback', intent));
    expect(oauth.searchParams.get('website_state')).toBe(state);
    expect(oauth.searchParams.get('website_callback')).toBe(callback);
    expect(oauth.searchParams.has('next')).toBe(false);
  });

  it.each([
    ['unlisted origin', { website_callback: 'https://evil.test/auth/callback' }],
    ['wrong path', { website_callback: `${website}/somewhere` }],
    ['injected query', { website_callback: `${callback}?code=injected` }],
    ['credentials', { website_callback: 'https://user@www.example.test/auth/callback' }],
    ['short state', { website_state: 'short' }],
    ['missing challenge', { code_challenge: '' }],
  ])('rejects a website intent with %s', (_name, overrides) => {
    expect(() => readLoginIntent(loginUrl(overrides))).toThrow(/PuppyOne website/);
  });

  it('is disabled when no website origin is configured', () => {
    vi.stubEnv('NEXT_PUBLIC_AUTH_WEBSITE_ORIGINS', '');
    expect(() => readLoginIntent(loginUrl())).toThrow();
  });

  it('accepts only exact HTTPS (or loopback HTTP) origins from configuration', () => {
    expect(allowedWebsiteOrigins('https://www.example.test, http://evil.test, https://x.test/path, http://localhost:3001/, nonsense'))
      .toEqual(['https://www.example.test', 'http://localhost:3001']);
  });

  it('OAuth callback relays the code to the website without exchanging it', async () => {
    const response = await GET(new Request(
      `https://web.example.test/auth/callback?website_state=${state}&website_callback=${encodeURIComponent(callback)}&code=provider-code`
    ));
    const target = new URL(response.headers.get('location')!);
    expect(target.origin + target.pathname).toBe(callback);
    expect(target.searchParams.get('code')).toBe('provider-code');
    expect(target.searchParams.get('state')).toBe(state);
    expect(mocks.server).not.toHaveBeenCalled();
    expect(mocks.setCookie).not.toHaveBeenCalled();
    expect(response.headers.get('cache-control')).toContain('no-store');
    expect(response.headers.get('referrer-policy')).toBe('no-referrer');
  });

  it('OAuth cancellation is relayed without provider detail', async () => {
    const response = await GET(new Request(
      `https://web.example.test/auth/callback?website_state=${state}&website_callback=${encodeURIComponent(callback)}&error=access_denied&error_description=private`
    ));
    const target = new URL(response.headers.get('location')!);
    expect(target.searchParams.get('error')).toBe('access_denied');
    expect(target.search).not.toContain('private');
  });

  it('never relays to an unlisted website and rejects mixed Desktop/website callbacks', async () => {
    const evil = await GET(new Request(
      `https://web.example.test/auth/callback?website_state=${state}&website_callback=${encodeURIComponent('https://evil.test/auth/callback')}&code=c`
    ));
    expect(evil.headers.get('location')).toBe('https://web.example.test/login?error=website_auth_request_expired');
    const mixed = await GET(new Request(
      `https://web.example.test/auth/callback?website_state=${state}&website_callback=${encodeURIComponent(callback)}&desktop_state=${state}&code=c`
    ));
    expect(mixed.headers.get('location')).toBe('https://web.example.test/login?error=auth_callback_failed');
    expect(mocks.server).not.toHaveBeenCalled();
  });

  it('omits injected credentials and rejects ambiguous website state', () => {
    const url = new URL(`https://web.test/auth/callback?website_state=${state}&website_callback=${encodeURIComponent(callback)}&code=code&access_token=secret`);
    expect(websiteCallbackTarget(url)).not.toContain('secret');
    url.searchParams.append('website_state', state);
    expect(() => websiteCallbackTarget(url)).toThrow();
  });

  it('website login middleware never touches the Web session and keeps origin referrer', async () => {
    const response = await middleware(new NextRequest(loginUrl().toString().replace('https://web.test', 'https://web.example.test'), {
      headers: { cookie: 'web-session=existing' },
    }));
    expect(response.status).toBe(200);
    expect(mocks.server).not.toHaveBeenCalled();
    expect(response.headers.get('set-cookie')).toBeNull();
    expect(response.headers.get('referrer-policy')).toBe('origin');
  });
});
