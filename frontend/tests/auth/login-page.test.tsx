import { StrictMode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  web: vi.fn(),
  desktop: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
  session: null as any,
  listeners: new Set<(event: string, session: any) => void>(),
  oauth: vi.fn(),
  exchange: vi.fn(),
  stop: vi.fn(),
  signOut: vi.fn(),
}));
vi.mock('@supabase/ssr', () => ({ createBrowserClient: mocks.web }));
vi.mock('@supabase/supabase-js', () => ({ createClient: mocks.desktop }));
vi.mock('next/navigation', () => ({
  usePathname: () => '/login',
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock('@/components/loading', () => ({
  PulseGrid: () => <span>Loading</span>,
  Dots: () => <span>Loading</span>,
}));
import LoginPage from '@/app/login/page';
import { SupabaseAuthProvider } from '@/contexts/SupabaseAuthProvider';

const state = 'a'.repeat(43);
const session = { access_token: 'access', refresh_token: 'refresh', user: { id: 'user-1' } };
let fetcher: ReturnType<typeof vi.fn>;
let enabled: string[];
let auth: any;
function publish() {
  mocks.session = session;
  mocks.listeners.forEach(listener => listener('SIGNED_IN', session));
  return { data: { session, user: session.user }, error: null };
}
function mount(desktop = false) {
  window.history.replaceState(
    {},
    '',
    desktop ? `/login?client=desktop&desktop_state=${state}&desktop_callback=http%3A%2F%2F127.0.0.1%3A43123%2Fauth%2Fcallback&code_challenge=${state}` : '/login?redirect=%2Finvite%2Fone'
  );
  return render(
    <StrictMode>
      <SupabaseAuthProvider>
        <LoginPage />
      </SupabaseAuthProvider>
    </StrictMode>
  );
}

beforeEach(() => {
  vi.stubEnv('NEXT_PUBLIC_SUPABASE_URL', 'https://auth.example.test');
  vi.stubEnv('NEXT_PUBLIC_SUPABASE_ANON_KEY', 'public-key');
  vi.stubEnv('NEXT_PUBLIC_APP_URL', 'http://localhost:3000');
  vi.stubEnv('NEXT_PUBLIC_AUTH_EMAIL_ONLY', 'false');
  sessionStorage.clear();
  mocks.session = null;
  mocks.listeners.clear();
  enabled = ['email', 'google', 'github'];
  auth = {
    getSession: vi.fn(async () => ({ data: { session: mocks.session }, error: null })),
    onAuthStateChange: vi.fn((listener: any) => {
      mocks.listeners.add(listener);
      return { data: { subscription: { unsubscribe: () => mocks.listeners.delete(listener) } } };
    }),
    signInWithPassword: vi.fn(async () => publish()),
    signUp: vi.fn(async () => ({ data: { user: session.user, session: null }, error: null })),
    verifyOtp: vi.fn(async () => publish()),
    signInWithOAuth: mocks.oauth.mockResolvedValue({ data: {}, error: null }),
    exchangeCodeForSession: mocks.exchange.mockImplementation(async () => publish()),
    stopAutoRefresh: mocks.stop,
    signOut: mocks.signOut,
  };
  mocks.web.mockReturnValue({ auth });
  mocks.desktop.mockReturnValue({ auth });
  fetcher = vi.fn(async (url: string) => {
    if (url.endsWith('/auth/check-email')) return Response.json({ data: { exists: !String(fetcher.mock.calls.at(-1)?.[1]?.body).includes('new@') } });
    if (url.endsWith('/auth/methods')) return Response.json({ data: { providers: enabled } });
    if (url.endsWith('/auth/initialize')) return Response.json({ data: {} });
    throw new Error('Unexpected request: ' + url);
  });
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it.each(['Google', 'GitHub'])(
  'Web %s button uses Supabase and preserves invite destination',
  async provider => {
    mount();
    const button = await screen.findByRole('button', { name: `Continue with ${provider}` });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(mocks.oauth).toHaveBeenCalledTimes(1));
    const options = mocks.oauth.mock.calls[0][0];
    expect(options.provider).toBe(provider.toLowerCase());
    expect(new URL(options.options.redirectTo).searchParams.get('next')).toBe('/invite/one');
    expect(mocks.web).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls.every(([url]) => !String(url).includes('/desktop/login'))).toBe(true);
    expect(mocks.desktop).not.toHaveBeenCalled(); // No temporary Desktop browser session
  }
);

it('email-only environment does not offer inactive provider buttons', async () => {
  enabled = ['email'];
  mount();
  await waitFor(() =>
    expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(
      false
    )
  );
  expect(screen.queryByText('Continue with Google')).toBeNull();
  expect(screen.queryByText('Continue with GitHub')).toBeNull();
});

it('Web email sign-in preserves invite destination and uses common initialization', async () => {
  mount(false);
  const input = screen.getByPlaceholderText('Your email address');
  await waitFor(() => expect((input as HTMLInputElement).disabled).toBe(false));
  fireEvent.change(input, { target: { value: 'user@example.test' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith('/auth/check-email'))).toBe(false);
  const password = await screen.findByPlaceholderText('Enter your password');
  fireEvent.change(password, { target: { value: 'password123' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/invite/one'));
  expect(
    fetcher.mock.calls.filter(([url]) => String(url).endsWith('/auth/initialize'))
  ).toHaveLength(1);
  expect(mocks.desktop).not.toHaveBeenCalled();
  expect(auth.signInWithPassword).toHaveBeenCalledWith({ email: 'user@example.test', password: 'password123' });
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('check-email'))).toBe(false);
});

it('Desktop uses the exact existing email-first UI and never reads Web cookies', async () => {
  mount(true);
  await waitFor(() => expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(false));
  expect(screen.queryByPlaceholderText('Enter your password')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Create an account' })).toBeNull();
  expect(mocks.desktop).toHaveBeenCalledOnce();
  expect(mocks.web).not.toHaveBeenCalled();
  const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {});
  fireEvent.change(screen.getByPlaceholderText('Your email address'), { target: { value: 'user@example.test' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  fireEvent.change(await screen.findByPlaceholderText('Enter your password'), { target: { value: 'password123' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));
  await waitFor(() => expect(submit).toHaveBeenCalledOnce());
  const form = submit.mock.instances[0] as HTMLFormElement;
  expect(form.method).toBe('post');
  expect(form.action).toBe('http://127.0.0.1:43123/auth/session');
  expect(new FormData(form).get('refresh_token')).toBe('refresh');
  expect(new FormData(form).get('state')).toBe(state);
  expect(form.action).not.toContain('refresh');
  expect(mocks.signOut).not.toHaveBeenCalled();
  expect(mocks.push).not.toHaveBeenCalled();
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('/auth/desktop'))).toBe(false);
});

it('failed Desktop initialization is actionable and cannot submit a half-initialized login', async () => {
  auth.getSession = vi.fn(async () => ({
    data: { session: null },
    error: new Error('Sign-in request expired. Start sign-in again from Desktop.'),
  }));
  mount(true);
  await screen.findByText('Sign-in request expired. Start sign-in again from Desktop.');
  expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(
    true
  );
});

it('Web email sign-in stays available when provider discovery fails', async () => {
  fetcher.mockImplementation(async (url: string) => {
    if (url.endsWith('/auth/methods')) return Response.json({ detail: 'down' }, { status: 503 });
    throw new Error('Unexpected request: ' + url);
  });
  mount();
  await waitFor(() =>
    expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(false)
  );
  expect(screen.queryByText('Continue with Google')).toBeNull();
  expect(screen.queryByText(/temporarily unavailable/i)).toBeNull();
});

it('a stale Web session does not block signing in again', async () => {
  auth.getSession = vi.fn(async () => ({
    data: { session: null },
    error: new Error('Invalid Refresh Token: Refresh Token Not Found'),
  }));
  mount();
  await waitFor(() =>
    expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(false)
  );
  expect(screen.queryByText(/Invalid Refresh Token/)).toBeNull();
});

it('email signup and OTP converge on the same Web completion', async () => {
  mount(false);
  const email = screen.getByPlaceholderText('Your email address');
  await waitFor(() => expect((email as HTMLInputElement).disabled).toBe(false));
  fireEvent.change(email, { target: { value: 'new@example.test' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  fireEvent.click(await screen.findByRole('button', { name: 'New to PuppyOne? Create an account' }));
  fireEvent.change(await screen.findByPlaceholderText('Create a password'), {
    target: { value: 'password123' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Create Account' }));
  fireEvent.change(await screen.findByPlaceholderText('123456'), { target: { value: '123456' } });
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/invite/one'));
  expect(auth.verifyOtp).toHaveBeenCalledOnce();
  expect(
    fetcher.mock.calls.filter(([url]) => String(url).endsWith('/auth/initialize'))
  ).toHaveLength(1);
});

describe('PuppyOne website sign-in', () => {
  const challenge = 'c'.repeat(43);
  function mountWebsite() {
    vi.stubEnv('NEXT_PUBLIC_AUTH_WEBSITE_ORIGINS', 'https://www.example.test');
    window.history.replaceState({}, '', '/login?' + new URLSearchParams({
      client: 'website', website_state: state,
      website_callback: 'https://www.example.test/auth/callback', code_challenge: challenge,
    }));
    return render(
      <StrictMode>
        <SupabaseAuthProvider>
          <LoginPage />
        </SupabaseAuthProvider>
      </StrictMode>
    );
  }

  it('uses the same email-first UI and hands an isolated session to the website', async () => {
    mountWebsite();
    await waitFor(() => expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(false));
    expect(mocks.desktop).toHaveBeenCalledOnce(); // temporary sessionStorage context
    expect(mocks.web).not.toHaveBeenCalled(); // Cloud Web cookies are never read
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {});
    fireEvent.change(screen.getByPlaceholderText('Your email address'), { target: { value: 'user@example.test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.change(await screen.findByPlaceholderText('Enter your password'), { target: { value: 'password123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    const form = submit.mock.instances[0] as HTMLFormElement;
    expect(form.method).toBe('post');
    expect(form.action).toBe('https://www.example.test/auth/session');
    expect(new FormData(form).get('refresh_token')).toBe('refresh');
    expect(new FormData(form).get('state')).toBe(state);
    expect(mocks.signOut).not.toHaveBeenCalled();
    expect(mocks.push).not.toHaveBeenCalled();
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith('/auth/initialize'))).toBe(false);
    expect(Object.keys(sessionStorage).some(key => key.startsWith('puppyone:website-auth:'))).toBe(false);
  });

  it('starts OAuth with the website challenge so only the website can redeem the code', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign, href: window.location.href });
    mountWebsite();
    const button = await screen.findByRole('button', { name: 'Continue with Google' });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(assign).toHaveBeenCalledOnce());
    const authorize = new URL(assign.mock.calls[0][0]);
    expect(authorize.origin + authorize.pathname).toBe('https://auth.example.test/auth/v1/authorize');
    expect(authorize.searchParams.get('code_challenge')).toBe(challenge);
    expect(authorize.searchParams.get('code_challenge_method')).toBe('s256');
    const redirect = new URL(authorize.searchParams.get('redirect_to')!);
    expect(redirect.pathname).toBe('/auth/callback');
    expect(redirect.searchParams.get('website_state')).toBe(state);
    expect(redirect.searchParams.get('website_callback')).toBe('https://www.example.test/auth/callback');
    expect(mocks.oauth).not.toHaveBeenCalled(); // the browser SDK would create its own verifier
  });

  it('an unlisted website origin cannot start a handoff', async () => {
    vi.stubEnv('NEXT_PUBLIC_AUTH_WEBSITE_ORIGINS', 'https://www.example.test');
    window.history.replaceState({}, '', '/login?' + new URLSearchParams({
      client: 'website', website_state: state,
      website_callback: 'https://evil.test/auth/callback', code_challenge: challenge,
    }));
    render(<SupabaseAuthProvider><LoginPage /></SupabaseAuthProvider>);
    expect(await screen.findAllByText(/Start sign-in again from the PuppyOne website/)).not.toHaveLength(0);
    expect(mocks.desktop).not.toHaveBeenCalled();
    expect(mocks.web).not.toHaveBeenCalled();
  });
});
