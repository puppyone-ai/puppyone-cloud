import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SWRConfig } from 'swr';
import { beforeEach, expect, it, vi } from 'vitest';
import { AccessManagement } from '@/features/access/lifecycle/AccessManagement';
import { AccessInvitation } from '@/features/access/lifecycle/AccessInvitation';
import { apiRawRequest } from '@/lib/apiClient';
import { invitationLink, readInvitation } from '@/features/access/lifecycle/api';

const auth = vi.hoisted(() => ({ userId: 'owner', session: { access_token: 'session', user: { id: 'owner', is_anonymous: false } }, isAuthReady: true }));
vi.mock('@/contexts/SupabaseAuthProvider', () => ({ useAuth: () => auth }));
vi.mock('@/lib/apiClient', () => ({ apiRawRequest: vi.fn() }));
vi.mock('@/lib/workspaceFiles', () => ({ workspaceStatus: async () => ({ workspace: { id: 'workspace-1', state: 'running' } }) }));
const token = `pai_${'a'.repeat(43)}`;
const initial = { id: 'request-1', state: 'pending', registration_name: 'Agent B', registration_id: 'client-1',
  applicant_user_id: 'guest', channels: ['ssh'], capabilities: ['workspace.read', 'workspace.write', 'workspace.shell'], target_kind: 'workspace' };
const wrap = (child: React.ReactNode) => <SWRConfig value={{ provider: () => new Map(), shouldRetryOnError: false, dedupingInterval: 0 }}>{child}</SWRConfig>;
beforeEach(() => {
  vi.mocked(apiRawRequest).mockReset();
  auth.userId = 'owner'; auth.session = { access_token: 'session', user: { id: 'owner', is_anonymous: false } };
  sessionStorage.clear(); window.history.replaceState(null, '', '/');
});

it('uses the same grant decision and revocation APIs for owner controls', async () => {
  vi.mocked(apiRawRequest).mockImplementation(async (path, options) => {
    if (options?.method !== 'GET') return {};
    if (path.endsWith('/requests')) return [initial];
    if (path.endsWith('/grants')) return [{ ...initial, id: 'grant-1', state: 'active' }];
    return [];
  });
  render(wrap(<AccessManagement projectId="project-1" />));
  await screen.findByRole('button', { name: 'Approve' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Duration for Agent B' }), { target: { value: 'session' } });
  fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
  await waitFor(() => expect(apiRawRequest).toHaveBeenCalledWith('/api/v1/projects/project-1/access/requests/request-1/decision', expect.objectContaining({
    method: 'POST', body: expect.stringContaining('"single_session":true'),
  })));
  await waitFor(() => expect((screen.getByRole('button', { name: 'Revoke' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'Revoke' }));
  await waitFor(() => expect(apiRawRequest).toHaveBeenCalledWith('/api/v1/projects/project-1/access/grants/grant-1', expect.objectContaining({ method: 'PATCH', body: '{"state":"revoked"}' })));
});

it('never exposes a late invitation after account changes', async () => {
  let finish!: (value: unknown) => void;
  vi.mocked(apiRawRequest).mockImplementation(async (_path, options) => {
    if (options?.method === 'POST') return new Promise(resolve => { finish = resolve; });
    return [];
  });
  const view = render(wrap(<AccessManagement projectId="project-1" />));
  fireEvent.click(screen.getByRole('button', { name: 'Create invitation' }));
  await waitFor(() => expect(finish).toBeDefined());
  auth.userId = 'other'; auth.session = { access_token: 'other-session', user: { id: 'other', is_anonymous: false } };
  view.rerender(wrap(<AccessManagement projectId="project-1" />));
  await act(async () => finish({}));
  expect(screen.queryByRole('textbox', { name: 'Invitation link' })).toBeNull();
});

it('recipient applies explicitly for an owned registration without a renewal secret', async () => {
  const url = invitationLink(token, window.location.origin);
  window.history.replaceState(null, '', new URL(url).pathname + new URL(url).hash);
  vi.mocked(apiRawRequest).mockImplementation(async (path, options) => {
    if (path.endsWith('/registrations')) return [{ id: 'client-1', name: 'Agent B', state: 'active' }];
    if (options?.method === 'POST') return initial;
    return [initial];
  });
  render(wrap(<AccessInvitation onConsumeFragment={() => window.history.replaceState(null, '', '/agent-access')} />));
  await screen.findByRole('button', { name: 'Request access' });
  expect(window.location.hash).toBe('');
  expect(apiRawRequest).not.toHaveBeenCalledWith('/api/v1/access/requests', expect.anything());
  fireEvent.click(screen.getByRole('button', { name: 'Request access' }));
  await screen.findByText('Waiting for approval');
  const call = vi.mocked(apiRawRequest).mock.calls.find(([path, opts]) => path === '/api/v1/access/requests' && opts?.method === 'POST');
  expect(JSON.parse(call![1]!.body as string)).toMatchObject({ registration_id: 'client-1', invitation: token });
  expect(sessionStorage.getItem('puppyone.access-invitation')).not.toContain('par_');
});

it('rejects an invitation for another service before making access requests', () => {
  expect(() => readInvitation(`#api=https%3A%2F%2Fevil.test&invitation=${token}`)).toThrow(/another service/);
});
