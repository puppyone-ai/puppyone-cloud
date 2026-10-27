import { StrictMode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const fixture = vi.hoisted(() => ({
  token: 'invite-one', session: { user: { id: 'invitee' } } as any,
  ready: true, accept: vi.fn(), signOut: vi.fn(), router: { replace: vi.fn() },
}));
vi.mock('next/navigation', () => ({ useParams: () => ({ token: fixture.token }), useRouter: () => fixture.router }));
vi.mock('@/contexts/SupabaseAuthProvider', () => ({ useAuth: () => ({ session: fixture.session, isAuthReady: fixture.ready, signOut: fixture.signOut }) }));
vi.mock('@/lib/organizationsApi', () => ({ acceptInvitation: fixture.accept }));
vi.mock('@/components/loading', () => ({ PulseGrid: () => null, Dots: () => null }));
import InviteAcceptPage from '@/app/invite/[token]/page';

beforeEach(() => {
  vi.clearAllMocks();
  fixture.token = 'invite-one'; fixture.session = { user: { id: 'invitee' } }; fixture.ready = true;
  window.history.replaceState({}, '', '/invite/invite-one');
  fixture.accept.mockResolvedValue({ org_id: 'org', org_name: 'Writing team' });
});
afterEach(() => vi.useRealTimers());
const page = () => <StrictMode><InviteAcceptPage /></StrictMode>;

it('waits for authentication and returns to the exact invitation after sign in', () => {
  fixture.session = null;
  render(page());
  expect(fixture.accept).not.toHaveBeenCalled();
  expect(fixture.router.replace).toHaveBeenCalledWith('/login?redirect=%2Finvite%2Finvite-one');
});

it('accepts exactly once in Strict Mode and cancels navigation when unmounted', async () => {
  vi.useFakeTimers();
  const view = render(page());
  await act(async () => {});
  expect(screen.getByText('Writing team')).toBeTruthy();
  expect(fixture.accept).toHaveBeenCalledExactlyOnceWith('invite-one');
  view.unmount();
  await act(() => vi.advanceTimersByTimeAsync(1500));
  expect(fixture.router.replace).not.toHaveBeenCalled();
});

it('shows wrong-account feedback and retries after account changes', async () => {
  fixture.accept.mockRejectedValueOnce(new Error('This invitation was issued to a different email address'));
  const view = render(page());
  await screen.findByText(/different email address/);
  fireEvent.click(screen.getByRole('button', { name: 'Use a different account' }));
  expect(fixture.signOut).toHaveBeenCalledOnce();
  fixture.session = { user: { id: 'correct-invitee' } };
  view.rerender(page());
  await screen.findByText('Writing team');
  expect(fixture.accept).toHaveBeenCalledTimes(2);
});

it('a stale successful response cannot replace the current invitation failure', async () => {
  let resolve!: (value: any) => void;
  fixture.accept.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  const view = render(page());
  fixture.token = 'revoked-invite';
  fixture.accept.mockRejectedValueOnce(new Error('Invitation not found or expired'));
  view.rerender(page());
  await screen.findByText('Invitation not found or expired');
  await act(async () => resolve({ org_id: 'old', org_name: 'Old team' }));
  await waitFor(() => expect(screen.queryByText('Old team')).toBeNull());
  expect(fixture.router.replace).not.toHaveBeenCalled();
});
