'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useOrganization } from '@/contexts/OrganizationContext';
import {
  inviteMember,
  updateMemberRole,
  removeMember,
  getInvitations,
  revokeInvitation,
  type OrgInvitation,
} from '@/lib/organizationsApi';
import { PageLoading, Dots, SkeletonBlock } from '@/components/loading';
import { OrganizationPageShell } from '@/components/organization/OrganizationPageShell';
import { ActivityIconButton } from '@/components/ActivityIconButton';
import { Mail, Send, Trash2, Check, Link as LinkIcon } from 'lucide-react';

const ROLE_LABELS: Record<string, string> = {
  owner: 'Owner',
  member: 'Member',
  viewer: 'Viewer',
};

const ROLE_COLORS: Record<string, string> = {
  owner: 'var(--po-warning)',
  member: 'var(--po-accent)',
  viewer: 'var(--po-text-subtle)',
};

function TeamMembersSkeleton() {
  return (
    <>
      {Array.from({ length: 3 }).map((_, index) => (
        <div
          key={index}
          className={`flex items-center justify-between px-5 py-4 ${index === 2 ? '' : 'border-b border-[var(--po-border-subtle)]'}`}
        >
          <div className="flex items-center gap-4">
            <SkeletonBlock width={36} height={36} radius={999} />
            <div className="flex flex-col gap-2">
              <SkeletonBlock width={140} height={11} radius={3} />
              <SkeletonBlock width={190} height={10} radius={3} />
            </div>
          </div>
          <SkeletonBlock width={72} height={12} radius={3} />
        </div>
      ))}
    </>
  );
}

export default function TeamPage() {
  const {
    orgs,
    currentOrg,
    members,
    myRole,
    isLoading: isOrgsLoading,
    error: orgsError,
    isMembersLoading,
    refreshMembers,
    refreshOrgs,
  } = useOrganization({ includeMembers: true });
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<'member' | 'viewer'>('member');
  const [inviting, setInviting] = useState(false);
  const [showInvite, setShowInvite] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'error' | 'success'; msg: string } | null>(null);

  // Pending invitations list. Owner-only; viewers / members see
  // nothing here. Loaded once per org change and refreshed after every
  // invite / revoke action so the list reflects the latest state
  // without polling.
  const [pendingInvites, setPendingInvites] = useState<OrgInvitation[]>([]);
  const [pendingLoading, setPendingLoading] = useState(false);
  // Token currently in the "Copied!" affordance for ~1.5s after click.
  // Keyed by invitation.id so multiple links can be independently
  // confirmed without colliding on a single boolean.
  const [copiedInviteId, setCopiedInviteId] = useState<string | null>(null);

  const isOwner = myRole === 'owner';

  const refreshPending = useCallback(async () => {
    if (!currentOrg || myRole !== 'owner') {
      setPendingInvites([]);
      return;
    }
    setPendingLoading(true);
    try {
      const list = await getInvitations(currentOrg.id);
      setPendingInvites(list);
    } catch (err) {
      // List failure is recoverable — show empty rather than blocking
      // the admin from inviting / managing existing members.
      console.error('[team] failed to load pending invitations:', err);
      setPendingInvites([]);
    } finally {
      setPendingLoading(false);
    }
  }, [currentOrg, myRole]);

  useEffect(() => {
    void refreshPending();
  }, [refreshPending]);

  const copyInviteLink = useCallback(async (invitation: OrgInvitation) => {
    const url = invitation.invite_url;
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopiedInviteId(invitation.id);
      setTimeout(() => {
        setCopiedInviteId((cur) => (cur === invitation.id ? null : cur));
      }, 1500);
    } catch (err) {
      // Clipboard API can be denied in iframe / non-HTTPS contexts.
      // Surface the URL so the user can manually select-and-copy.
      console.error('[team] clipboard write failed:', err);
      setFeedback({
        type: 'error',
        msg: 'Could not copy automatically — invite URL: ' + url,
      });
    }
  }, []);

  const handleRevoke = useCallback(
    async (invitation: OrgInvitation) => {
      if (!currentOrg) return;
      if (!confirm(`Revoke the invitation for ${invitation.email}?`)) return;
      try {
        await revokeInvitation(currentOrg.id, invitation.id);
        await refreshPending();
        setFeedback({ type: 'success', msg: `Invitation for ${invitation.email} revoked` });
        setTimeout(() => setFeedback(null), 3000);
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to revoke invitation';
        setFeedback({ type: 'error', msg });
      }
    },
    [currentOrg, refreshPending],
  );

  const handleInvite = async () => {
    if (!currentOrg || !inviteEmail.trim()) return;
    setInviting(true);
    setFeedback(null);
    try {
      const invitation = await inviteMember(currentOrg.id, inviteEmail.trim(), inviteRole);
      // Build the success message based on whether the backend's
      // best-effort email actually went out. When it didn't (existing
      // user, SMTP not configured, etc.) the row still exists and the
      // URL is copyable — but tell the admin so they share it
      // manually instead of waiting for a non-existent email.
      const msg = invitation.email_sent
        ? `Invitation emailed to ${invitation.email}. The link is also copyable below.`
        : invitation.email_error === 'recipient_exists'
          ? `${invitation.email} already has a Puppyone account — share the link below to add them to this organization.`
          : `Invitation created for ${invitation.email}. Email delivery is unavailable in this environment — share the link below.`;
      setFeedback({ type: 'success', msg });
      setInviteEmail('');
      setShowInvite(false);
      await Promise.all([refreshMembers(), refreshPending()]);
      // Auto-copy the new invite URL so the admin can paste it
      // immediately. Best-effort: clipboard failures are silent here
      // because the URL is also visible in the pending-invites list.
      if (invitation.invite_url) {
        try {
          await navigator.clipboard.writeText(invitation.invite_url);
          setCopiedInviteId(invitation.id);
          setTimeout(() => {
            setCopiedInviteId((cur) => (cur === invitation.id ? null : cur));
          }, 1500);
        } catch {
          // Surfaced in the pending list anyway.
        }
      }
      // Hold the feedback longer than the usual 3s — the admin needs
      // time to read which case applied and act on it.
      setTimeout(() => setFeedback(null), 6000);
    } catch (err: any) {
      setFeedback({ type: 'error', msg: err.message || 'Failed to send invitation' });
    } finally {
      setInviting(false);
    }
  };

  const handleRoleChange = async (userId: string, newRole: string) => {
    if (!currentOrg) return;
    try {
      await updateMemberRole(currentOrg.id, userId, newRole);
      await refreshMembers();
      setFeedback({ type: 'success', msg: 'Role updated successfully' });
      setTimeout(() => setFeedback(null), 3000);
    } catch (err: any) {
      setFeedback({ type: 'error', msg: err.message || 'Failed to update role' });
    }
  };

  const handleRemove = async (userId: string, displayName: string) => {
    if (!currentOrg) return;
    if (!confirm(`Remove ${displayName} from the organization?`)) return;
    try {
      await removeMember(currentOrg.id, userId);
      await refreshMembers();
      setFeedback({ type: 'success', msg: 'Member removed successfully' });
      setTimeout(() => setFeedback(null), 3000);
    } catch (err: any) {
      setFeedback({ type: 'error', msg: err.message || 'Failed to remove member' });
    }
  };

  // No current org resolved yet. Distinguish the cases so this never spins
  // forever: a failed org-list load (or an empty account) must show an
  // actionable state, not an endless loader.
  if (!currentOrg) {
    if (orgsError) {
      return (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
          <div className="text-[14px] font-medium text-[var(--po-text)]">
            Couldn’t load your organizations
          </div>
          <div className="max-w-md text-[13px] text-[var(--po-text-subtle)]">
            {orgsError.message || 'The request failed. Your session may have expired — try again, or sign in if it keeps failing.'}
          </div>
          <button
            onClick={() => { void refreshOrgs(); }}
            className="flex h-8 items-center gap-2 rounded-md bg-[var(--po-text)] px-3 text-[14px] font-medium text-[var(--po-text-inverse)] transition-colors hover:opacity-90"
          >
            Retry
          </button>
        </div>
      );
    }
    if (isOrgsLoading || orgs.length > 0) {
      // still loading, or orgs are loaded and currentOrg is resolving this tick
      return (
        <div className="flex-1">
          <PageLoading variant="fill" />
        </div>
      );
    }
    // Loaded, no error, no orgs → the account genuinely has no organization.
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
        <div className="text-[14px] font-medium text-[var(--po-text)]">No organization yet</div>
        <div className="max-w-md text-[13px] text-[var(--po-text-subtle)]">
          You’re not a member of any organization. Create one or accept an invitation to get started.
        </div>
      </div>
    );
  }

  return (
    <OrganizationPageShell
      title="Team"
      description={`Manage members and their access to ${currentOrg.name}.`}
      actions={
        isOwner && !showInvite ? (
          <button
            onClick={() => setShowInvite(true)}
            className="flex h-8 items-center gap-2 rounded-md bg-[var(--po-text)] px-3 text-[14px] font-medium text-[var(--po-text-inverse)] transition-colors hover:opacity-90"
          >
            <Mail size={14} strokeWidth={2} />
            Invite Member
          </button>
        ) : null
      }
    >
        {/* Feedback */}
        {feedback && (
          <div
            className="mb-6 flex items-center justify-between rounded-[8px] border px-4 py-3 text-[13px] font-medium"
            style={{
              animation: 'dialog-fade-in 0.2s ease-out',
              borderColor: feedback.type === 'error'
                ? 'color-mix(in srgb, var(--po-danger) 28%, transparent)'
                : 'color-mix(in srgb, var(--po-success) 28%, transparent)',
              background: feedback.type === 'error'
                ? 'color-mix(in srgb, var(--po-danger) 8%, transparent)'
                : 'color-mix(in srgb, var(--po-success) 8%, transparent)',
              color: feedback.type === 'error' ? 'var(--po-danger)' : 'var(--po-success)',
            }}
          >
            {feedback.msg}
            <ActivityIconButton
              kind="close"
              title="Dismiss"
              size="sm"
              onClick={() => setFeedback(null)}
            />
          </div>
        )}

        {/* Main Card */}
        <div className="overflow-hidden rounded-[8px] border border-[var(--po-border)] bg-[var(--po-panel)]">

          {/* Card Header */}
          <div className="flex items-center justify-between border-b border-[var(--po-border)] px-5 py-4">
            <div>
              <h2 className="mb-1 text-[14px] font-medium text-[var(--po-text)]">Members</h2>
              <div className="text-[13px] text-[var(--po-text-subtle)]">
                {members.length} / {currentOrg.seat_limit} seats used in your {currentOrg.plan} plan.
              </div>
            </div>
          </div>

          {/* Invite Form */}
          {showInvite && (
            <div className="border-b border-[var(--po-border)] bg-[var(--po-control)] px-5 py-4" style={{ animation: 'dialog-fade-in 0.15s ease-out' }}>
              <div className="flex gap-3">
                <input
                  type="email"
                  placeholder="Email address"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  className="h-8 flex-1 rounded-md border border-[var(--po-border)] bg-[var(--po-inset)] px-3 text-[14px] text-[var(--po-text)] outline-none transition-colors placeholder:text-[var(--po-text-disabled)] focus:border-[var(--po-border-strong)]"
                  onKeyDown={(e) => e.key === 'Enter' && handleInvite()}
                  autoFocus
                />
                <select
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value as 'member' | 'viewer')}
                  className="h-8 w-32 rounded-md border border-[var(--po-border)] bg-[var(--po-inset)] px-3 text-[14px] text-[var(--po-text)] outline-none transition-colors focus:border-[var(--po-border-strong)]"
                >
                  <option value="member">Member</option>
                  <option value="viewer">Viewer</option>
                </select>
                <button
                  onClick={handleInvite}
                  disabled={inviting || !inviteEmail.trim()}
                  className={`flex h-8 items-center gap-2 rounded-md bg-[var(--po-text)] px-3 text-[14px] font-medium text-[var(--po-text-inverse)] transition-colors ${inviting || !inviteEmail.trim() ? 'cursor-not-allowed opacity-50' : 'hover:opacity-90'}`}
                >
                  {inviting ? <Dots size="xs" /> : <Send size={14} strokeWidth={2} />}
                  {inviting ? 'Sending…' : 'Send Invite'}
                </button>
                <button
                  onClick={() => setShowInvite(false)}
                  className="flex h-8 items-center gap-2 rounded-md border border-[var(--po-border)] bg-[var(--po-panel-raised)] px-3 text-[14px] font-medium text-[var(--po-text-muted)] transition-colors hover:bg-[var(--po-hover)]"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Pending Invitations — owner-only. Always rendered while
              there's at least one row so the admin can copy/share
              the URL, even when email delivery worked (sometimes
              invitees never see the email and the admin needs to
              re-send the link by other means). */}
          {isOwner && (pendingLoading || pendingInvites.length > 0) && (
            <div className="border-b border-[var(--po-border)] bg-[var(--po-panel-raised)] px-5 py-4">
              <div className="mb-3 flex items-center justify-between">
                <div className="text-[13px] font-medium text-[var(--po-text-muted)]">
                  Pending invitations
                </div>
                {pendingLoading && <Dots size="xs" />}
              </div>
              <div className="flex flex-col gap-2">
                {pendingInvites.map((inv) => {
                  const copied = copiedInviteId === inv.id;
                  return (
                    <div
                      key={inv.id}
                      className="flex items-center gap-3 rounded-md border border-[var(--po-border-subtle)] bg-[var(--po-inset)] px-3 py-2"
                    >
                      <Mail size={14} strokeWidth={2} className="shrink-0 text-[var(--po-text-subtle)]" />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[13px] text-[var(--po-text)]">
                          {inv.email}
                        </div>
                        <div className="text-[11px] text-[var(--po-text-subtle)]">
                          {inv.role} · invited{' '}
                          {new Date(inv.created_at).toLocaleDateString()}
                          {inv.email_sent === false && (
                            <span className="ml-1 text-[var(--po-warning)]">
                              · email not delivered
                            </span>
                          )}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => copyInviteLink(inv)}
                        disabled={!inv.invite_url}
                        title={inv.invite_url ?? 'Invite URL unavailable'}
                        className="flex h-7 items-center gap-1.5 rounded-md border border-[var(--po-border)] bg-[var(--po-control)] px-2 text-[12px] font-medium text-[var(--po-text)] transition-colors hover:bg-[var(--po-hover)] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {copied ? (
                          <>
                            <Check size={12} strokeWidth={2.4} />
                            Copied
                          </>
                        ) : (
                          <>
                            <LinkIcon size={12} strokeWidth={2} />
                            Copy link
                          </>
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleRevoke(inv)}
                        title="Revoke invitation"
                        className="flex h-7 w-7 items-center justify-center rounded-md text-[var(--po-text-subtle)] transition-colors hover:bg-[var(--po-hover)] hover:text-[var(--po-danger)]"
                      >
                        <Trash2 size={13} strokeWidth={2} />
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Members List */}
          <div className="flex flex-col">
            {isMembersLoading && members.length === 0 ? (
              <TeamMembersSkeleton />
            ) : members.map((m, index) => {
              const name = m.display_name || m.email || 'Unknown member';
              const initial = (name[0] || '?').toUpperCase();
              const isLast = index === members.length - 1;

              return (
                <div key={m.id} className={`flex items-center justify-between px-5 py-4 ${isLast ? '' : 'border-b border-[var(--po-border-subtle)]'}`}>
                  <div className="flex items-center gap-4">
                    {m.avatar_url ? (
                      <img src={m.avatar_url} alt="" className="h-9 w-9 rounded-full object-cover" />
                    ) : (
                      <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[var(--po-control)] text-[13px] font-semibold text-[var(--po-text-muted)]">
                        {initial}
                      </div>
                    )}
                    <div>
                      <div className="flex items-center gap-2 text-[14px] font-medium text-[var(--po-text)]">
                        {name}
                        {m.role === 'owner' && (
                          <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--po-warning)] bg-[color-mix(in_srgb,var(--po-warning)_12%,transparent)]">Owner</span>
                        )}
                      </div>
                      {m.email && m.display_name && (
                        <div className="mt-0.5 text-[13px] text-[var(--po-text-subtle)]">{m.email}</div>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-4">
                    {isOwner && m.role !== 'owner' ? (
                      <>
                        <div className="relative">
                          <select
                            value={m.role}
                            onChange={(e) => handleRoleChange(m.user_id, e.target.value)}
                            className="h-8 cursor-pointer appearance-none border-transparent bg-transparent py-1.5 pl-3 pr-7 text-[14px] outline-none"
                            style={{ color: ROLE_COLORS[m.role] || 'var(--po-text)' }}
                          >
                            <option value="member" className="text-[var(--po-text)] bg-[var(--po-inset)]">Member</option>
                            <option value="viewer" className="text-[var(--po-text)] bg-[var(--po-inset)]">Viewer</option>
                          </select>
                          <div className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--po-text-disabled)]">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
                          </div>
                        </div>
                        <div className="h-5 w-px bg-[var(--po-border)]"></div>
                        <button
                          onClick={() => handleRemove(m.user_id, name)}
                          className="flex h-[30px] w-[30px] items-center justify-center p-0 text-[var(--po-text-subtle)] transition-colors hover:text-[var(--po-danger)]"
                          title="Remove member"
                        >
                          <Trash2 size={14} strokeWidth={2} />
                        </button>
                      </>
                    ) : (
                      <span className="text-[14px] font-medium" style={{ color: ROLE_COLORS[m.role] || 'var(--po-text-subtle)' }}>
                        {ROLE_LABELS[m.role] || m.role}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

      <style jsx>{`
        @keyframes dialog-fade-in {
          from { opacity: 0; transform: translateY(-4px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </OrganizationPageShell>
  );
}
