import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SWRConfig } from 'swr';
import { beforeEach, expect, it, vi } from 'vitest';
import { AgentConnectApproval } from '@/features/access/lifecycle/AgentConnectApproval';
import { apiRawRequest } from '@/lib/apiClient';

const auth = vi.hoisted(() => ({
  userId: 'owner' as string | null, isAuthReady: true,
  session: { user: { id: 'owner', is_anonymous: false } } as { user: { id: string; is_anonymous: boolean } } | null,
}));
vi.mock('@/contexts/SupabaseAuthProvider', () => ({ useAuth: () => auth }));
vi.mock('@/lib/apiClient', () => ({ apiRawRequest: vi.fn() }));

const pending = { id: 'c1', name: 'Claude Code', state: 'pending', project_name: 'Launch plan', grant_seconds: 2592000 };
const wrap = () => render(<SWRConfig value={{ provider: () => new Map(), shouldRetryOnError: false, dedupingInterval: 0 }}>
  <AgentConnectApproval connectionId="c1" />
</SWRConfig>);

beforeEach(() => {
  vi.mocked(apiRawRequest).mockReset();
  auth.userId = 'owner';
  auth.session = { user: { id: 'owner', is_anonymous: false } };
});

it('sends a signed-out person through login and back to this request', () => {
  auth.userId = null;
  auth.session = null;
  wrap();
  expect(screen.getByRole('link', { name: 'Sign in' }).getAttribute('href'))
    .toBe('/login?redirect=%2Fagent-connect%2Fc1');
  expect(apiRawRequest).not.toHaveBeenCalled();
});

it('treats an anonymous session as signed out', () => {
  auth.session = { user: { id: 'owner', is_anonymous: true } };
  wrap();
  expect(screen.getByRole('link', { name: 'Sign in' })).toBeTruthy();
});

it('says what the agent will be able to do before it is connected, then connects once', async () => {
  vi.mocked(apiRawRequest).mockImplementation(async (_path, options) => (
    options?.method === 'POST' ? { ...pending, state: 'approved' } : pending
  ));
  wrap();
  await screen.findByRole('heading', { name: /Connect “Claude Code” to Launch plan/ });
  expect(screen.getByText(/run commands and edit every file/).textContent).toContain('for 30 days');
  fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
  await screen.findByRole('heading', { name: 'Connected' });
  expect(apiRawRequest).toHaveBeenCalledWith('/api/v1/agent-links/connections/c1/decision', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ decision: 'approve' }),
  }));
  expect(vi.mocked(apiRawRequest).mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
});

it('declines without connecting', async () => {
  vi.mocked(apiRawRequest).mockImplementation(async (_path, options) => (
    options?.method === 'POST' ? { ...pending, state: 'rejected' } : pending
  ));
  wrap();
  fireEvent.click(await screen.findByRole('button', { name: 'Decline' }));
  await screen.findByRole('heading', { name: 'Request declined' });
  expect(apiRawRequest).toHaveBeenCalledWith('/api/v1/agent-links/connections/c1/decision', expect.objectContaining({
    body: JSON.stringify({ decision: 'reject' }),
  }));
});

it('tells anyone but the owner that they cannot allow it, and offers another account', async () => {
  vi.mocked(apiRawRequest).mockRejectedValue(Object.assign(new Error('denied'), { status: 403 }));
  wrap();
  await screen.findByRole('heading', { name: 'You can’t allow this agent' });
  expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull();
  expect(screen.getByRole('link', { name: 'Use another account' }).getAttribute('href'))
    .toBe('/login?redirect=%2Fagent-connect%2Fc1');
});

it('explains a request that can no longer be acted on', async () => {
  vi.mocked(apiRawRequest).mockResolvedValue({ ...pending, state: 'expired' });
  wrap();
  await screen.findByRole('heading', { name: 'This request expired' });
  expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull();
});

it('keeps the decision available when it fails', async () => {
  let calls = 0;
  vi.mocked(apiRawRequest).mockImplementation(async (_path, options) => {
    if (options?.method === 'POST') { calls += 1; throw new Error('offline'); }
    return pending;
  });
  wrap();
  fireEvent.click(await screen.findByRole('button', { name: 'Connect' }));
  await waitFor(() => expect(calls).toBe(1));
  expect((await screen.findByRole('alert')).textContent).toContain('Try again');
  expect((screen.getByRole('button', { name: 'Connect' }) as HTMLButtonElement).disabled).toBe(false);
});
