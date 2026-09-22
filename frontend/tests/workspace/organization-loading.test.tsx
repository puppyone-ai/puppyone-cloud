import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { SWRConfig } from 'swr';
import { beforeEach, expect, it, vi } from 'vitest';
import { OrganizationProvider, useOrganization } from '@/contexts/OrganizationContext';
import { getOrganizations, getMembers, type OrganizationInfo } from '@/lib/organizationsApi';

const auth = vi.hoisted(() => ({ session: {}, userId: 'alice', isAuthReady: true }));
vi.mock('@/contexts/SupabaseAuthProvider', () => ({ useAuth: () => auth }));
vi.mock('@/lib/organizationsApi', () => ({ getOrganizations: vi.fn(), getMembers: vi.fn() }));

function wrapper() {
  const cache = new Map();
  return function Test({ children }: { children: ReactNode }) {
    return <SWRConfig value={{ provider: () => cache, shouldRetryOnError: false }}><OrganizationProvider>{children}</OrganizationProvider></SWRConfig>;
  };
}
beforeEach(() => { auth.userId = 'alice'; vi.mocked(getOrganizations).mockReset(); vi.mocked(getMembers).mockReset().mockResolvedValue([]); });

it('keeps identity loading explicit and selects a valid org without loading its members', async () => {
  let resolve!: (orgs: OrganizationInfo[]) => void;
  vi.mocked(getOrganizations).mockReturnValue(new Promise(done => { resolve = done; }));
  localStorage.setItem('puppyone_current_org:alice', 'revoked-org');
  const hook = renderHook(() => useOrganization(), { wrapper: wrapper() });
  expect(hook.result.current.isLoading).toBe(true);
  expect(hook.result.current.currentOrg).toBeNull();
  await act(async () => resolve([{ id: 'allowed' } as OrganizationInfo]));
  expect(hook.result.current.currentOrg?.id).toBe('allowed');
  expect(hook.result.current.isLoading).toBe(false);
  expect(getMembers).not.toHaveBeenCalled();
});

it('fetches members only for the consumer that explicitly needs them', async () => {
  vi.mocked(getOrganizations).mockResolvedValue([{ id: 'org' } as OrganizationInfo]);
  const hook = renderHook(({ includeMembers }) => useOrganization({ includeMembers }), { wrapper: wrapper(), initialProps: { includeMembers: false } });
  await waitFor(() => expect(hook.result.current.currentOrg?.id).toBe('org'));
  expect(getMembers).not.toHaveBeenCalled();
  hook.rerender({ includeMembers: true });
  await waitFor(() => expect(getMembers).toHaveBeenCalledExactlyOnceWith('org'));
});

it('does not reuse another signed-in user’s organization snapshot', async () => {
  vi.mocked(getOrganizations).mockResolvedValueOnce([{ id: 'alice-org' } as OrganizationInfo]).mockImplementation(() => new Promise(() => {}));
  const hook = renderHook(() => useOrganization(), { wrapper: wrapper() });
  await waitFor(() => expect(hook.result.current.currentOrg?.id).toBe('alice-org'));
  auth.userId = 'bob';
  hook.rerender();
  expect(hook.result.current.currentOrg).toBeNull();
  expect(hook.result.current.isLoading).toBe(true);
});
