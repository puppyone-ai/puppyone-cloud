import { StrictMode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * `/share/{token}` is one of two pre-account surfaces (the other is
 * `/invite/{token}`), so its contract is behavioural, not cosmetic:
 *
 *   - it always reaches a terminal, readable state;
 *   - no state is reachable only through a spinner;
 *   - every failure the join endpoint can produce is attributable.
 *
 * The regression these cover: the page used to render "Loading share
 * link…" for both "auth is booting" and "you are signed out", and had
 * no deadline on either, so an auth bootstrap failure left it spinning
 * with no way out.
 */

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  push: vi.fn(),
  join: vi.fn(),
  signOut: vi.fn(),
  auth: {
    session: null as any,
    isAuthReady: false,
    authError: null as string | null,
  },
  router: null as any,
}));
mocks.router = { replace: mocks.replace, push: mocks.push };

// A stable router object on purpose: an unstable one would mask a
// re-subscription loop in the page rather than expose it.
vi.mock('next/navigation', () => ({
  useRouter: () => mocks.router,
  useParams: () => ({ token: 'share-token-1' }),
  usePathname: () => '/share/share-token-1',
}));
vi.mock('@/contexts/SupabaseAuthProvider', () => ({
  useAuth: () => ({ ...mocks.auth, signOut: mocks.signOut }),
}));
vi.mock('@/lib/projectsApi', () => ({ joinProjectViaShareToken: mocks.join }));
vi.mock('@/components/loading', () => ({ Dots: () => <span>busy</span> }));

import SharePage from '@/app/share/[token]/page';

function mount() {
  return render(
    <StrictMode>
      <SharePage />
    </StrictMode>
  );
}

function httpError(status: number, message: string) {
  return Object.assign(new Error(message), { status });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  mocks.auth = { session: null, isAuthReady: false, authError: null };
  mocks.join.mockReset();
  mocks.replace.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('share link landing page', () => {
  it('offers a sign-in action instead of bouncing a signed-out visitor through a spinner', async () => {
    mocks.auth = { session: null, isAuthReady: true, authError: null };
    mount();

    const link = await screen.findByRole('link', { name: /sign in to continue/i });
    expect(link.getAttribute('href')).toBe('/login?redirect=%2Fshare%2Fshare-token-1');
    // The visitor is never silently redirected: the card is the surface.
    expect(mocks.replace).not.toHaveBeenCalled();
    expect(mocks.join).not.toHaveBeenCalled();
  });

  it('escapes the auth bootstrap instead of spinning forever', async () => {
    mocks.auth = { session: null, isAuthReady: false, authError: null };
    mount();

    expect(screen.getByText(/preparing your session/i)).toBeTruthy();
    await vi.advanceTimersByTimeAsync(13_000);
    await waitFor(() => screen.getByText(/couldn't start the session/i));
    expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy();
  });

  it('reports a provider bootstrap failure immediately', async () => {
    mocks.auth = { session: null, isAuthReady: false, authError: 'Supabase is not configured' };
    mount();

    await waitFor(() => screen.getByText(/couldn't start the session/i));
    expect(screen.getByText('Supabase is not configured')).toBeTruthy();
  });

  it('joins once and lands the member inside the project', async () => {
    mocks.auth = { session: { user: { email: 'a@b.test' } }, isAuthReady: true, authError: null };
    mocks.join.mockResolvedValue({
      project_id: 'p1',
      project_name: 'Atlas',
      role: 'viewer',
      newly_joined: true,
    });
    mount();

    await waitFor(() => screen.getByText('Joined Atlas'));
    // Strict Mode replays effects; the POST must not double-fire.
    expect(mocks.join).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(mocks.replace).toHaveBeenCalledWith('/projects/p1/data');
  });

  it('explains a revoked link and does not offer a pointless retry', async () => {
    mocks.auth = { session: { user: {} }, isAuthReady: true, authError: null };
    mocks.join.mockRejectedValue(httpError(404, 'Share link is invalid or has been rotated'));
    mount();

    await waitFor(() => screen.getByText(/no longer works/i));
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();
    expect(screen.getByRole('button', { name: /use a different account/i })).toBeTruthy();
  });

  it('explains the organization boundary using the server message', async () => {
    mocks.auth = { session: { user: {} }, isAuthReady: true, authError: null };
    mocks.join.mockRejectedValue(
      httpError(403, "This share link only works for members of the project's organization.")
    );
    mount();

    await waitFor(() => screen.getByText(/for another organization/i));
    expect(
      screen.getByText(/only works for members of the project's organization/i)
    ).toBeTruthy();
  });

  it('times out a hung join instead of spinning', async () => {
    mocks.auth = { session: { user: {} }, isAuthReady: true, authError: null };
    mocks.join.mockReturnValue(new Promise(() => {}));
    mount();

    await waitFor(() => screen.getByText(/adding you to the project/i));
    await vi.advanceTimersByTimeAsync(36_000);
    await waitFor(() => screen.getByText(/taking too long/i));
    expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy();
  });

  it('retries the join on demand', async () => {
    mocks.auth = { session: { user: {} }, isAuthReady: true, authError: null };
    mocks.join.mockRejectedValueOnce(httpError(500, 'boom')).mockResolvedValueOnce({
      project_id: 'p2',
      project_name: 'Beta',
      role: 'viewer',
      newly_joined: false,
    });
    mount();

    await waitFor(() => screen.getByText(/couldn't open this project/i));
    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    await waitFor(() => screen.getByText("You're already in Beta"));
    expect(mocks.join).toHaveBeenCalledTimes(2);
  });
});
