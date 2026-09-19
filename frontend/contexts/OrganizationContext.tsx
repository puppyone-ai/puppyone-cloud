'use client';

import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import useSWR from 'swr';
import {
  getOrganizations,
  getMembers,
  type OrganizationInfo,
  type OrgMember,
} from '@/lib/organizationsApi';
import { useAuth } from '@/app/supabase/SupabaseAuthProvider';

interface OrganizationContextValue {
  orgs: OrganizationInfo[];
  currentOrg: OrganizationInfo | null;
  isLoading: boolean;
  /** Set when the org list failed to load — lets pages show an error/retry
   *  state instead of spinning forever waiting for a `currentOrg` that will
   *  never arrive. */
  error: Error | null;
  switchOrg: (orgId: string) => void;
  refreshOrgs: () => Promise<void>;
}

const OrganizationContext = createContext<OrganizationContextValue | null>(null);
const EMPTY_ORGS: OrganizationInfo[] = [];
const EMPTY_MEMBERS: OrgMember[] = [];

export function OrganizationProvider({ children }: { children: React.ReactNode }) {
  const { session, userId, isAuthReady } = useAuth();
  const [selection, setSelection] = useState<{ userId: string; orgId: string } | null>(null);

  const {
    data: orgs = EMPTY_ORGS,
    isLoading: isOrgsLoading,
    error: orgsError,
    mutate: mutateOrgs,
  } = useSWR(
    session && userId ? ['organizations', userId] : null,
    () => getOrganizations(),
    { dedupingInterval: 30000, revalidateOnFocus: false }
  );

  // Select in the render that receives the list, not one effect/render later.
  // Stored preferences are hints only and must belong to the authorized list.
  const storageKey = `puppyone_current_org:${userId}`;
  let stored: string | null = null;
  try {
    stored = typeof window !== 'undefined' ? localStorage.getItem(storageKey) : null;
  } catch { /* Storage can be disabled; selection remains usable. */ }
  const preferredId = selection?.userId === userId ? selection.orgId : stored;
  const currentOrg = orgs.find(o => o.id === preferredId) ?? orgs[0] ?? null;
  const currentOrgId = currentOrg?.id;

  const refreshOrgs = useCallback(async () => {
    await mutateOrgs();
  }, [mutateOrgs]);

  useEffect(() => {
    if (!currentOrgId || !userId) return;
    try { localStorage.setItem(storageKey, currentOrgId); } catch { /* optional */ }
  }, [currentOrgId, storageKey, userId]);

  const switchOrg = useCallback((orgId: string) => {
    if (userId) setSelection({ userId, orgId });
  }, [userId]);

  return (
    <OrganizationContext.Provider
      value={{
        orgs,
        currentOrg,
        isLoading: !isAuthReady || isOrgsLoading,
        error: (orgsError as Error) ?? null,
        switchOrg,
        refreshOrgs,
      }}
    >
      {children}
    </OrganizationContext.Provider>
  );
}

export function useOrganization({ includeMembers = false } = {}) {
  const ctx = useContext(OrganizationContext);
  const { userId } = useAuth();
  const orgId = ctx?.currentOrg?.id;
  // Only Team / member management needs the member directory. Merely mounting
  // the workspace must not enumerate every member (and their profile lookups).
  const { data: members = EMPTY_MEMBERS, isLoading, error, mutate } = useSWR(
    includeMembers && userId && orgId ? ['org-members', userId, orgId] : null,
    () => getMembers(orgId!),
    { dedupingInterval: 30000, revalidateOnFocus: false },
  );
  const refreshMembers = useCallback(async () => { await mutate(); }, [mutate]);
  if (!ctx) {
    throw new Error('useOrganization must be used within OrganizationProvider');
  }
  return {
    ...ctx,
    members,
    myRole: members.find(member => member.user_id === userId)?.role ?? null,
    isMembersLoading: includeMembers && (ctx.isLoading || isLoading),
    membersError: error,
    refreshMembers,
  };
}
