'use client';

import { useState, useEffect, useMemo } from 'react';
import type { CSSProperties } from 'react';
import { useRouter } from 'next/navigation';
import useSWR from 'swr';
import { useAuth } from '@/app/supabase/SupabaseAuthProvider';
import { useProject, useProjects, refreshProjects } from '@/lib/hooks/useData';
import { useOrganization } from '@/contexts/OrganizationContext';
import { PROJECT_CONTENT_RAIL_WIDTH } from '@/lib/layout';
import { ProjectManageDialog } from '@/components/ProjectManageDialog';
import { ActivityIconButton } from '@/components/ActivityIconButton';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import {
  getProjectMembers,
  addProjectMember,
  updateProjectMemberRole,
  removeProjectMember,
  updateProject,
  updateProjectVisibility,
  getProjectShareInfo,
  rotateProjectShareToken,
  projectAllows,
  type ProjectMember,
} from '@/lib/projectsApi';
import { useTranslations } from 'next-intl';
import { PageLoading, SkeletonBlock } from '@/components/loading';
import { DialogRoot, DialogSurface } from '@/components/ui/Dialog';
import { ProjectSettingsDialogFrame } from './ProjectSettingsDialogFrame';

const T = {
  bg: 'var(--po-canvas)',
  border: 'var(--po-border)',
  cardBg: 'var(--po-panel)',
  cardBorder: 'var(--po-border-subtle)',
  text1: 'var(--po-text)',
  text2: 'var(--po-text-muted)',
  text3: 'var(--po-text-disabled)',
  text4: 'var(--po-filetree-rail)',
  fontSans:
    'var(--po-font-sans)',
  fontMono:
    'var(--po-font-mono)',
  ease: 'cubic-bezier(0.16, 1, 0.3, 1)',
} as const;

// Role-tag colors. Kept saturated on purpose — these are user-facing
// permission signals, not chrome, so they need to register at a glance
// against the neutral page background.
const ROLE_COLORS: Record<string, string> = {
  admin: 'var(--po-warning)',
  editor: 'var(--po-accent)',
  viewer: 'var(--po-text-subtle)',
};

// SVG Icons for better UI
const AddUserIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
    <circle cx="8.5" cy="7" r="4"></circle>
    <line x1="20" y1="8" x2="20" y2="14"></line>
    <line x1="23" y1="11" x2="17" y2="11"></line>
  </svg>
);

const TrashIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="3 6 5 6 21 6"></polyline>
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
  </svg>
);

const CopyIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
  </svg>
);

const EditIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
  </svg>
);

function memberDisplayName(member: { display_name?: string | null; email?: string | null; user_id?: string }) {
  if (member.display_name) return member.display_name;
  if (member.email) return member.email;
  // Better fallback than literal "Unknown member" — render the user_id
  // prefix so the row is at least identifiable across the list. Real
  // fix is the backend pulling email from auth.users; this is the
  // last-resort visible string when even that fails.
  if (member.user_id) return `User ${member.user_id.slice(0, 8)}`;
  return 'Unknown member';
}

function ProjectMembersSkeleton() {
  return (
    <>
      {Array.from({ length: 2 }).map((_, index) => (
        <div
          key={index}
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '10px 12px',
            borderRadius: 7,
            background: 'var(--po-control)',
            border: `1px solid ${T.cardBorder}`,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <SkeletonBlock width={28} height={28} radius={999} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
              <SkeletonBlock width={132} height={10} radius={3} />
              <SkeletonBlock width={176} height={9} radius={3} />
            </div>
          </div>
          <SkeletonBlock width={70} height={10} radius={3} />
        </div>
      ))}
    </>
  );
}

export default function ProjectSettingsDialog({ projectId, onClose }: {
  projectId: string;
  onClose: () => void;
}) {
  const router = useRouter();
  const { session, isAuthReady } = useAuth();
  const {
    currentOrg,
    members: orgMembers,
    isMembersLoading: orgMembersLoading,
  } = useOrganization({ includeMembers: true });

  const { project: routeProject, isLoading: routeProjectLoading } = useProject(session ? projectId : null);
  const { projects, isLoading } = useProjects(currentOrg?.id ?? null);
  const currentProject = projects.find(p => p.id === projectId) ?? routeProject;
  const canManageSettings = projectAllows(currentProject, 'project.settings.manage');
  const canManageMembers = projectAllows(currentProject, 'project.member.manage');
  const scopedProjects = useMemo(() => {
    const projectsForCurrentRoute =
      routeProject?.org_id && currentOrg?.id !== routeProject.org_id ? [] : projects;
    if (!routeProject || projectsForCurrentRoute.some(p => p.id === routeProject.id)) {
      return projectsForCurrentRoute;
    }
    return [routeProject, ...projectsForCurrentRoute];
  }, [currentOrg?.id, projects, routeProject]);

  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  const {
    data: projectMembers = [],
    isLoading: projectMembersLoading,
    mutate: mutateProjectMembers,
  } =
    useSWR<ProjectMember[]>(
      session && canManageMembers ? ['project-members', projectId] : null,
      async () => {
        try {
          return await getProjectMembers(projectId);
        } catch {
          // project_members table might not exist yet
          return [];
        }
      },
      { revalidateOnFocus: false },
    );
  const [visibility, setVisibility] = useState<'org' | 'private'>('org');

  // Inline editor state for ``bound_git_branch``. Mirrors what the
  // backend has when no edit is in flight; the input only commits on
  // explicit save so a stray keystroke can't mutate the project.
  const tProjSettings = useTranslations('projectSettings');
  const [boundBranchInput, setBoundBranchInput] = useState('');
  const [boundBranchEditing, setBoundBranchEditing] = useState(false);
  const [boundBranchSaving, setBoundBranchSaving] = useState(false);
  const [showAddMember, setShowAddMember] = useState(false);
  // Bulk-add modal state. We keep the legacy single-select pieces
  // (selectedUserId / addRole) only as the role default fallback —
  // the new flow is multi-select keyed by user_id in a Set.
  const [selectedUserId, setSelectedUserId] = useState('');
  const [addRole, setAddRole] = useState<'admin' | 'editor' | 'viewer'>('editor');
  const [bulkSelectedUserIds, setBulkSelectedUserIds] = useState<Set<string>>(() => new Set());
  const [bulkSearch, setBulkSearch] = useState('');
  const [bulkAddBusy, setBulkAddBusy] = useState(false);

  // Share link state. Only fetched/rendered when the current user can
  // share (owner/admin); the GET returns 403 otherwise, which we treat
  // as "no share UI for this user".
  const [shareToken, setShareToken] = useState<string | null>(null);
  const [shareLoading, setShareLoading] = useState(false);
  const [shareRotating, setShareRotating] = useState(false);
  const [shareCopied, setShareCopied] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'error' | 'success'; msg: string } | null>(null);
  const [memberRemoval, setMemberRemoval] = useState<{ userId: string; name: string } | null>(null);
  const [memberRemovalLoading, setMemberRemovalLoading] = useState(false);

  useEffect(() => {
    if (currentProject?.visibility) {
      setVisibility(currentProject.visibility as 'org' | 'private');
    }
  }, [currentProject?.visibility]);

  useEffect(() => {
    // Don't trample an in-flight edit if the projects list refetches
    // while the user is mid-keystroke.
    if (!boundBranchEditing) {
      setBoundBranchInput(currentProject?.bound_git_branch || 'main');
    }
  }, [currentProject?.bound_git_branch, boundBranchEditing]);

  const handleBoundBranchSave = async () => {
    const next = boundBranchInput.trim();
    if (!next) {
      setFeedback({ type: 'error', msg: 'Branch cannot be empty' });
      return;
    }
    if (next === (currentProject?.bound_git_branch || 'main')) {
      setBoundBranchEditing(false);
      return;
    }
    setBoundBranchSaving(true);
    try {
      await updateProject(projectId, { bound_git_branch: next });
      await refreshProjects();
      setBoundBranchEditing(false);
      setFeedback({ type: 'success', msg: 'Default branch updated.' });
      setTimeout(() => setFeedback(null), 2500);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to update branch';
      setFeedback({ type: 'error', msg });
    } finally {
      setBoundBranchSaving(false);
    }
  };

  const handleVisibilityChange = async (newVis: 'org' | 'private') => {
    try {
      await updateProjectVisibility(projectId, newVis);
      setVisibility(newVis);
      setFeedback({ type: 'success', msg: `Project is now ${newVis === 'org' ? 'visible to the organization' : 'private'}.` });
      setTimeout(() => setFeedback(null), 3000);
    } catch (err: any) {
      setFeedback({ type: 'error', msg: err.message || 'Failed to update visibility' });
    }
  };

  const handleAddMember = async () => {
    if (!selectedUserId) return;
    try {
      await addProjectMember(projectId, selectedUserId, addRole);
      setShowAddMember(false);
      setSelectedUserId('');
      await mutateProjectMembers();
      setFeedback({ type: 'success', msg: 'Member added successfully.' });
      setTimeout(() => setFeedback(null), 3000);
    } catch (err: any) {
      setFeedback({ type: 'error', msg: err.message || 'Failed to add member' });
    }
  };

  const handleRoleChange = async (userId: string, newRole: string) => {
    try {
      await updateProjectMemberRole(projectId, userId, newRole);
      await mutateProjectMembers();
    } catch (err: any) {
      setFeedback({ type: 'error', msg: err.message || 'Failed to update role' });
    }
  };

  const handleConfirmRemoveMember = async () => {
    if (!memberRemoval) return;
    setMemberRemovalLoading(true);
    try {
      await removeProjectMember(projectId, memberRemoval.userId);
      await mutateProjectMembers();
    } catch (err: any) {
      setFeedback({ type: 'error', msg: err.message || 'Failed to remove member' });
    } finally {
      setMemberRemovalLoading(false);
      setMemberRemoval(null);
    }
  };

  const existingUserIds = new Set(projectMembers.map(m => m.user_id));
  const availableOrgMembers = orgMembers.filter(m => !existingUserIds.has(m.user_id));

  // Org-member search: case-insensitive substring over display_name +
  // email. Empty query = show all available org members.
  const filteredOrgMembers = useMemo(() => {
    const q = bulkSearch.trim().toLowerCase();
    if (!q) return availableOrgMembers;
    return availableOrgMembers.filter(m => {
      const haystack = `${m.display_name ?? ''} ${m.email ?? ''}`.toLowerCase();
      return haystack.includes(q);
    });
  }, [availableOrgMembers, bulkSearch]);

  const canShare = canManageMembers;

  // Fetch the share token for owners/admins only. Re-runs when the
  // project id changes OR the user's role changes (signing back in
  // as a different account, etc.).
  useEffect(() => {
    if (!canShare || !projectId) {
      setShareToken(null);
      return;
    }
    let cancelled = false;
    setShareLoading(true);
    getProjectShareInfo(projectId)
      .then(info => {
        if (!cancelled) setShareToken(info.share_token);
      })
      .catch(() => {
        // 403 / 404 — surface as "no share link available" rather than
        // a feedback toast; the user often doesn't know if their role
        // qualifies and a red error here would be confusing.
        if (!cancelled) setShareToken(null);
      })
      .finally(() => {
        if (!cancelled) setShareLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, canShare]);

  const shareUrl = useMemo(() => {
    if (!shareToken) return '';
    if (typeof window === 'undefined') return '';
    return `${window.location.origin}/share/${shareToken}`;
  }, [shareToken]);

  const openAddMemberModal = () => {
    setBulkSelectedUserIds(new Set());
    setBulkSearch('');
    setAddRole('editor');
    setShowAddMember(true);
  };

  const toggleBulkSelect = (userId: string) => {
    setBulkSelectedUserIds(prev => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  };

  const handleBulkAdd = async () => {
    if (bulkSelectedUserIds.size === 0) return;
    setBulkAddBusy(true);
    // Add each selected user in parallel. The backend doesn't have a
    // bulk endpoint yet (intentional MVP scope), but the per-call
    // latency is small + the org member list is bounded; parallel
    // POST is acceptable here.
    const userIds = Array.from(bulkSelectedUserIds);
    const results = await Promise.allSettled(
      userIds.map(uid => addProjectMember(projectId, uid, addRole)),
    );
    const failed = results
      .map((r, i) => (r.status === 'rejected' ? userIds[i] : null))
      .filter((x): x is string => x !== null);
    setBulkAddBusy(false);
    if (failed.length === 0) {
      setShowAddMember(false);
      await mutateProjectMembers();
      setFeedback({
        type: 'success',
        msg: `Added ${userIds.length} member${userIds.length === 1 ? '' : 's'}.`,
      });
      setTimeout(() => setFeedback(null), 3000);
    } else {
      await mutateProjectMembers();
      setFeedback({
        type: 'error',
        msg: `${userIds.length - failed.length} added, ${failed.length} failed. Check roles and try again.`,
      });
    }
  };

  const handleCopyShareLink = async () => {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setShareCopied(true);
      setTimeout(() => setShareCopied(false), 1500);
    } catch {
      setFeedback({ type: 'error', msg: `Couldn't copy — link: ${shareUrl}` });
    }
  };

  const handleRotateShareLink = async () => {
    if (!projectId) return;
    if (!confirm('Rotate the share link? The current link will stop working immediately.')) return;
    setShareRotating(true);
    try {
      const info = await rotateProjectShareToken(projectId);
      setShareToken(info.share_token);
      setFeedback({ type: 'success', msg: 'Share link rotated. Previous link is invalid.' });
      setTimeout(() => setFeedback(null), 3000);
    } catch (err: any) {
      setFeedback({ type: 'error', msg: err.message || 'Failed to rotate share link' });
    } finally {
      setShareRotating(false);
    }
  };

  const nestedDialogOpen = editDialogOpen || deleteDialogOpen || showAddMember || memberRemoval !== null;
  const settingsBusy = boundBranchSaving || bulkAddBusy || shareRotating || memberRemovalLoading;
  const closeSettings = () => {
    if (!nestedDialogOpen && !settingsBusy) onClose();
  };
  const dismissTopDialog = () => {
    if (settingsBusy) return;
    if (editDialogOpen) setEditDialogOpen(false);
    else if (deleteDialogOpen) setDeleteDialogOpen(false);
    else if (showAddMember) setShowAddMember(false);
    else if (memberRemoval) setMemberRemoval(null);
    else if (boundBranchEditing) {
      setBoundBranchEditing(false);
      setBoundBranchInput(currentProject?.bound_git_branch || 'main');
    }
    else onClose();
  };
  const frameProps = {
    projectName: currentProject?.name,
    onClose: closeSettings,
    onEscape: dismissTopDialog,
    nestedDialogOpen,
  };

  if (!isAuthReady || isLoading || routeProjectLoading || !currentProject) {
    return <ProjectSettingsDialogFrame {...frameProps}><PageLoading variant="fill" /></ProjectSettingsDialogFrame>;
  }

  if (!canManageSettings) {
    return (
      <ProjectSettingsDialogFrame {...frameProps}>
      <div style={{ display: 'grid', placeItems: 'center', height: '100%', background: T.bg, padding: 32 }}>
        <div style={{ maxWidth: 460, textAlign: 'center', color: T.text2, fontFamily: T.fontSans }}>
          <h1 style={{ margin: '0 0 8px', color: T.text1, fontSize: 18 }}>
            Project settings are restricted
          </h1>
          <p style={{ margin: '0 0 18px', fontSize: 13, lineHeight: 1.6 }}>
            Your current Project role can view this Project, but it cannot manage settings, members, sharing, or deletion.
          </p>
          <button type="button" onClick={onClose} style={btnGhost}>
            Close settings
          </button>
        </div>
      </div>
      </ProjectSettingsDialogFrame>
    );
  }

  return (
    <ProjectSettingsDialogFrame {...frameProps}>
    <div
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: T.bg,
        overflow: 'hidden',
        fontFamily: T.fontSans,
      }}
    >
      <div className='workspace-settings-body' style={{ flex: 1, minHeight: 0, overflowY: 'auto', overscrollBehavior: 'contain', padding: '24px' }}>
        <div style={{ maxWidth: PROJECT_CONTENT_RAIL_WIDTH, margin: '0 auto' }}>

          {/* Feedback toast — softer than before. Both the
              background and the border use lower-alpha versions of
              the semantic color so the toast reads as a tinted card,
              not a saturated alert. The close glyph is a real "x"
              now, not a trash can. */}
          {feedback && (
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '10px 14px',
                marginBottom: 20,
                borderRadius: 7,
                fontSize: 12.5,
                fontWeight: 500,
                lineHeight: 1.5,
                fontFamily: T.fontSans,
                background:
                  feedback.type === 'error'
                    ? 'color-mix(in srgb, var(--po-danger) 8%, transparent)'
                    : 'color-mix(in srgb, var(--po-success) 8%, transparent)',
                border: `1px solid ${feedback.type === 'error' ? 'color-mix(in srgb, var(--po-danger) 24%, transparent)' : 'color-mix(in srgb, var(--po-success) 24%, transparent)'}`,
                color: feedback.type === 'error' ? 'var(--po-danger)' : 'var(--po-success)',
                animation: 'dialog-fade-in 0.2s ease-out',
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

          {/* Section: General */}
          <div style={{ marginBottom: 48 }}>
            <h2 style={sectionTitle}>General</h2>
            <div style={cardBox}>
              <div className="workspace-settings-row" style={{ ...row, borderBottom: `1px solid ${T.cardBorder}` }}>
                <div>
                  <label style={labelStyle}>Project Name</label>
                  <div style={descStyle}>Used to identify your project in the dashboard.</div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span style={{ fontSize: 13, color: T.text1, fontWeight: 500 }}>{currentProject.name}</span>
                  <button
                    onClick={() => setEditDialogOpen(true)}
                    onMouseEnter={onGhostEnter}
                    onMouseLeave={onGhostLeave}
                    style={btnGhost}
                  >
                    <EditIcon /> Edit
                  </button>
                </div>
              </div>
              <div className="workspace-settings-row" style={{ ...row, borderBottom: `1px solid ${T.cardBorder}` }}>
                <div>
                  <label style={labelStyle}>{tProjSettings('boundGitBranch')}</label>
                  <div style={descStyle}>{tProjSettings('boundGitBranchHint')}</div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {boundBranchEditing ? (
                    <>
                      <input
                        type="text"
                        aria-label={tProjSettings('boundGitBranch')}
                        autoFocus
                        value={boundBranchInput}
                        onChange={(e) => setBoundBranchInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') void handleBoundBranchSave();
                          if (e.key === 'Escape') {
                            setBoundBranchEditing(false);
                            setBoundBranchInput(currentProject.bound_git_branch || 'main');
                          }
                        }}
                        disabled={boundBranchSaving}
                        style={{
                          background: 'var(--po-control)',
                          border: `1px solid ${T.cardBorder}`,
                          borderRadius: 5,
                          color: T.text1,
                          fontFamily: T.fontMono,
                          fontSize: 12,
                          padding: '5px 9px',
                          width: 180,
                          outline: 'none',
                        }}
                      />
                      <button
                        onClick={() => void handleBoundBranchSave()}
                        disabled={boundBranchSaving}
                        onMouseEnter={onGhostEnter}
                        onMouseLeave={onGhostLeave}
                        style={btnGhost}
                      >
                        {boundBranchSaving ? '…' : 'Save'}
                      </button>
                      <button
                        onClick={() => {
                          setBoundBranchEditing(false);
                          setBoundBranchInput(currentProject.bound_git_branch || 'main');
                        }}
                        disabled={boundBranchSaving}
                        onMouseEnter={onGhostEnter}
                        onMouseLeave={onGhostLeave}
                        style={btnGhost}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      <code
                        style={{
                          fontSize: 12,
                          color: T.text1,
                          background: 'var(--po-control)',
                          padding: '5px 9px',
                          borderRadius: 5,
                          border: `1px solid ${T.cardBorder}`,
                          fontFamily: T.fontMono,
                        }}
                      >
                        {currentProject.bound_git_branch || 'main'}
                      </code>
                      <button
                        onClick={() => setBoundBranchEditing(true)}
                        onMouseEnter={onGhostEnter}
                        onMouseLeave={onGhostLeave}
                        style={btnGhost}
                      >
                        <EditIcon /> Edit
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="workspace-settings-row" style={row}>
                <div>
                  <label style={labelStyle}>Project ID</label>
                  <div style={descStyle}>Unique identifier for API access.</div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <code
                    style={{
                      fontSize: 11,
                      color: T.text2,
                      background: 'var(--po-control)',
                      padding: '5px 9px',
                      borderRadius: 5,
                      border: `1px solid ${T.cardBorder}`,
                      fontFamily: T.fontMono,
                    }}
                  >
                    {currentProject.id}
                  </code>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(currentProject.id);
                      setFeedback({ type: 'success', msg: 'Project ID copied to clipboard' });
                      setTimeout(() => setFeedback(null), 2000);
                    }}
                    onMouseEnter={onGhostEnter}
                    onMouseLeave={onGhostLeave}
                    style={btnGhost}
                  >
                    <CopyIcon /> Copy
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Section: Access */}
          {canManageMembers && (
            <div style={{ marginBottom: 48 }}>
              <h2 style={sectionTitle}>Access & Visibility</h2>
              <div style={cardBox}>

                {/* Visibility — segmented control. The earlier pill
                    (a dark literal over `var(--po-filetree-rail)`) read as a
                    second card embedded inside the section card,
                    competing with the surface itself. The flatter
                    treatment below — translucent track + translucent
                    active fill — sits more quietly inside the row. */}
                <div className="workspace-settings-row" style={{ ...row, borderBottom: `1px solid ${T.cardBorder}`, paddingBottom: 20 }}>
                  <div>
                    <label style={labelStyle}>Project Visibility</label>
                    <div style={descStyle}>
                      {visibility === 'org'
                        ? 'Anyone in the organization can view and collaborate on this project.'
                        : 'Only organization owners and explicitly invited members can access this project.'}
                    </div>
                  </div>
                  <div
                    style={{
                      display: 'flex',
                      background: 'var(--po-hover)',
                      border: `1px solid ${T.cardBorder}`,
                      borderRadius: 7,
                      padding: 3,
                    }}
                  >
                    <button
                      onClick={() => handleVisibilityChange('org')}
                      style={{
                        height: 30,
                        padding: '0 14px',
                        background:
                          visibility === 'org'
                            ? 'var(--po-border)'
                            : 'transparent',
                        color: visibility === 'org' ? T.text1 : T.text2,
                        border: 'none',
                        borderRadius: 5,
                        fontSize: 12,
                        fontWeight: 500,
                        fontFamily: T.fontSans,
                        cursor: 'pointer',
                        transition: `all 0.15s ${T.ease}`,
                      }}
                    >
                      Organization
                    </button>
                    <button
                      onClick={() => handleVisibilityChange('private')}
                      style={{
                        height: 30,
                        padding: '0 14px',
                        background:
                          visibility === 'private'
                            ? 'var(--po-border)'
                            : 'transparent',
                        color: visibility === 'private' ? T.text1 : T.text2,
                        border: 'none',
                        borderRadius: 5,
                        fontSize: 12,
                        fontWeight: 500,
                        fontFamily: T.fontSans,
                        cursor: 'pointer',
                        transition: `all 0.15s ${T.ease}`,
                      }}
                    >
                      Private
                    </button>
                  </div>
                </div>

                {/* Members */}
                <div style={{ padding: 20 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <div>
                      <label style={labelStyle}>Project Members</label>
                      {visibility === 'org' && (
                        <div style={{ fontSize: 12, color: T.text2, marginTop: 2, lineHeight: 1.55 }}>
                          Members added here will retain access if visibility is changed to Private.
                        </div>
                      )}
                    </div>
                    {!orgMembersLoading && availableOrgMembers.length > 0 && (
                      <button
                        onClick={openAddMemberModal}
                        onMouseEnter={onPrimaryEnter}
                        onMouseLeave={onPrimaryLeave}
                        style={btnPrimary}
                      >
                        <AddUserIcon /> Add Member
                      </button>
                    )}
                  </div>

                  {/* Share link block — only owner/admin sees this; the
                      load-share-info effect 404/403s for everyone else
                      and ``shareToken`` stays null. Renders for both
                      visibilities because even org-mode projects
                      sometimes need a way to onboard someone before
                      they exist in the org member list. */}
                  {canShare && (
                    <div
                      style={{
                        padding: 12,
                        marginBottom: 16,
                        background: 'var(--po-control)',
                        borderRadius: 7,
                        border: `1px solid ${T.cardBorder}`,
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 8,
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 500, color: T.text1 }}>Share link</div>
                          <div style={{ fontSize: 11, color: T.text3, marginTop: 2 }}>
                            Anyone with this link can join as Viewer.
                            {visibility === 'org' && ' Useful for onboarding people who aren’t in the org yet.'}
                          </div>
                        </div>
                        <button
                          onClick={handleRotateShareLink}
                          disabled={shareRotating || !shareToken}
                          onMouseEnter={onGhostEnter}
                          onMouseLeave={onGhostLeave}
                          style={{
                            ...btnGhost,
                            opacity: shareRotating || !shareToken ? 0.5 : 1,
                            cursor: shareRotating || !shareToken ? 'not-allowed' : 'pointer',
                            fontSize: 12,
                          }}
                          title="Generates a new link and invalidates the current one"
                        >
                          {shareRotating ? 'Rotating…' : 'Rotate'}
                        </button>
                      </div>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <input
                          type="text"
                          readOnly
                          value={shareLoading ? 'Loading…' : shareUrl || '(unavailable)'}
                          style={{
                            flex: 1,
                            padding: '6px 10px',
                            borderRadius: 6,
                            border: `1px solid ${T.cardBorder}`,
                            background: 'var(--po-inset)',
                            color: T.text2,
                            fontSize: 12,
                            fontFamily: T.fontMono,
                          }}
                          onClick={e => e.currentTarget.select()}
                        />
                        <button
                          onClick={handleCopyShareLink}
                          disabled={!shareUrl}
                          onMouseEnter={shareUrl ? onPrimaryEnter : undefined}
                          onMouseLeave={shareUrl ? onPrimaryLeave : undefined}
                          style={{
                            ...btnPrimary,
                            opacity: shareUrl ? 1 : 0.45,
                            cursor: shareUrl ? 'pointer' : 'not-allowed',
                            fontSize: 12,
                          }}
                        >
                          {shareCopied ? 'Copied!' : 'Copy'}
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Bulk-add modal — single source of truth for adding
                      members. Replaces the previous inline single-select
                      form. Search + multi-checkbox + role + Add N. */}
                  {showAddMember && (
                    <DialogRoot layer="modalNested" onClose={() => setShowAddMember(false)} dismissOnBackdrop={!bulkAddBusy}>
                      <DialogSurface width={480} maxHeight="80vh" ariaLabel="Add project members" style={{ background: 'var(--po-panel)' }}>
                        <div style={{
                          padding: '14px 16px',
                          borderBottom: `1px solid ${T.cardBorder}`,
                          fontSize: 14, fontWeight: 600, color: T.text1,
                        }}>
                          Add members from {currentOrg?.name ?? 'organization'}
                        </div>
                        <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10, flexShrink: 0 }}>
                          <input
                            type="text"
                            placeholder="Search by name or email…"
                            value={bulkSearch}
                            onChange={e => setBulkSearch(e.target.value)}
                            autoFocus
                            style={{
                              padding: '8px 10px',
                              borderRadius: 6,
                              border: `1px solid ${T.cardBorder}`,
                              background: 'var(--po-inset)',
                              color: T.text1,
                              fontSize: 13,
                            }}
                          />
                        </div>
                        <div style={{
                          flex: 1, minHeight: 0, overflowY: 'auto',
                          padding: '0 14px 8px 14px',
                          display: 'flex', flexDirection: 'column', gap: 4,
                        }}>
                          {filteredOrgMembers.length === 0 && (
                            <div style={{ fontSize: 12, color: T.text3, padding: '14px 4px', textAlign: 'center' }}>
                              {availableOrgMembers.length === 0
                                ? 'Everyone in the organization is already a member.'
                                : 'No members match your search.'}
                            </div>
                          )}
                          {filteredOrgMembers.map(m => {
                            const checked = bulkSelectedUserIds.has(m.user_id);
                            return (
                              <label
                                key={m.user_id}
                                style={{
                                  display: 'flex', alignItems: 'center', gap: 10,
                                  padding: '8px 8px',
                                  borderRadius: 6,
                                  cursor: 'pointer',
                                  background: checked ? 'var(--po-selected)' : 'transparent',
                                }}
                                onMouseEnter={e => { if (!checked) e.currentTarget.style.background = 'var(--po-hover)'; }}
                                onMouseLeave={e => { if (!checked) e.currentTarget.style.background = 'transparent'; }}
                              >
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  onChange={() => toggleBulkSelect(m.user_id)}
                                  style={{ flexShrink: 0 }}
                                />
                                <div style={{ flex: 1, minWidth: 0 }}>
                                  <div style={{
                                    fontSize: 13, color: T.text1,
                                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                  }}>
                                    {memberDisplayName(m)}
                                  </div>
                                  {m.email && m.display_name && (
                                    <div style={{ fontSize: 11, color: T.text3 }}>{m.email}</div>
                                  )}
                                </div>
                                <span style={{ fontSize: 10, color: T.text3, textTransform: 'capitalize' }}>
                                  {m.role}
                                </span>
                              </label>
                            );
                          })}
                        </div>
                        <div style={{
                          padding: 14, borderTop: `1px solid ${T.cardBorder}`,
                          display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0,
                        }}>
                          <select
                            value={addRole}
                            onChange={e => setAddRole(e.target.value as 'admin' | 'editor' | 'viewer')}
                            style={{ ...selectStyle, maxWidth: 130 }}
                            disabled={bulkAddBusy}
                          >
                            <option value="admin">Admin</option>
                            <option value="editor">Editor</option>
                            <option value="viewer">Viewer</option>
                          </select>
                          <div style={{ flex: 1 }} />
                          <button
                            onClick={() => setShowAddMember(false)}
                            onMouseEnter={onGhostEnter}
                            onMouseLeave={onGhostLeave}
                            disabled={bulkAddBusy}
                            style={btnGhost}
                          >
                            Cancel
                          </button>
                          <button
                            onClick={handleBulkAdd}
                            disabled={bulkSelectedUserIds.size === 0 || bulkAddBusy}
                            onMouseEnter={bulkSelectedUserIds.size > 0 ? onPrimaryEnter : undefined}
                            onMouseLeave={bulkSelectedUserIds.size > 0 ? onPrimaryLeave : undefined}
                            style={{
                              ...btnPrimary,
                              opacity: bulkSelectedUserIds.size > 0 && !bulkAddBusy ? 1 : 0.5,
                              cursor: bulkSelectedUserIds.size > 0 && !bulkAddBusy ? 'pointer' : 'not-allowed',
                            }}
                          >
                            {bulkAddBusy
                              ? 'Adding…'
                              : bulkSelectedUserIds.size > 0
                                ? `Add ${bulkSelectedUserIds.size} member${bulkSelectedUserIds.size === 1 ? '' : 's'}`
                                : 'Add'}
                          </button>
                        </div>
                      </DialogSurface>
                    </DialogRoot>
                  )}

                  {/* Members list */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {projectMembersLoading && projectMembers.length === 0 ? (
                      <ProjectMembersSkeleton />
                    ) : projectMembers.length === 0 && !showAddMember && (
                      <div
                        style={{
                          fontSize: 12,
                          color: T.text3,
                          padding: '20px 0',
                          textAlign: 'center',
                          border: `1px dashed ${T.cardBorder}`,
                          borderRadius: 7,
                        }}
                      >
                        No project-specific members yet.
                      </div>
                    )}
                    {projectMembers.map(m => {
                      const name = memberDisplayName(m);
                      const initial = (name[0] || '?').toUpperCase();
                      return (
                        <div
                          key={m.id}
                          className='workspace-settings-member'
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            padding: '10px 12px',
                            borderRadius: 7,
                            background: 'var(--po-control)',
                            border: `1px solid ${T.cardBorder}`,
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            {m.avatar_url ? (
                              <img src={m.avatar_url} alt="" style={{ width: 28, height: 28, borderRadius: '50%', objectFit: 'cover' }} />
                            ) : (
                              <div
                                style={{
                                  width: 28,
                                  height: 28,
                                  borderRadius: '50%',
                                  background: 'var(--po-border-subtle)',
                                  display: 'flex',
                                  alignItems: 'center',
                                  justifyContent: 'center',
                                  fontSize: 12,
                                  fontWeight: 600,
                                  color: T.text2,
                                }}
                              >
                                {initial}
                              </div>
                            )}
                            <div>
                              <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--po-text)' }}>{name}</div>
                              {m.email && m.display_name && (
                                <div style={{ fontSize: 11, color: T.text3, marginTop: 2 }}>{m.email}</div>
                              )}
                            </div>
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <div style={{ position: 'relative' }}>
                              <select
                                value={m.role}
                                onChange={e => handleRoleChange(m.user_id, e.target.value)}
                                style={{
                                  ...selectStyle,
                                  padding: '4px 22px 4px 8px',
                                  height: 30,
                                  fontSize: 11.5,
                                  fontWeight: 500,
                                  color: ROLE_COLORS[m.role] || 'var(--po-text)',
                                  background: 'transparent',
                                  borderColor: 'transparent',
                                  cursor: 'pointer',
                                  appearance: 'none',
                                  WebkitAppearance: 'none',
                                }}
                              >
                                <option value="admin">Admin</option>
                                <option value="editor">Editor</option>
                                <option value="viewer">Viewer</option>
                              </select>
                              <div style={{ position: 'absolute', right: 6, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: T.text3 }}>
                                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
                              </div>
                            </div>
                            <div style={{ width: 1, height: 14, background: T.cardBorder }}></div>
                            <button
                              onClick={() => setMemberRemoval({ userId: m.user_id, name })}
                              style={{ width: 30, height: 30, background: 'none', border: 'none', cursor: 'pointer', color: T.text3, padding: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', transition: `color 0.15s ${T.ease}` }}
                              title="Remove member"
                              onMouseEnter={e => (e.currentTarget.style.color = 'var(--po-danger)')}
                              onMouseLeave={e => (e.currentTarget.style.color = T.text3)}
                            >
                              <TrashIcon />
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Section: Danger Zone — same surface metrics as the
              other sections (so the page rhythm stays consistent),
              just tinted with a low-alpha red so it still reads as
              the "do not enter" zone. The button itself is the
              loudest element in the section, not the card chrome. */}
          <div>
            <h2 style={{ ...sectionTitle, color: 'var(--po-danger)' }}>Danger Zone</h2>
            <div
              style={{
                border: '1px solid color-mix(in srgb, var(--po-danger) 20%, transparent)',
                borderRadius: 8,
                overflow: 'hidden',
                background: 'color-mix(in srgb, var(--po-danger) 5%, transparent)',
              }}
            >
              <div className="workspace-settings-row" style={row}>
                <div>
                  <label style={{ ...labelStyle, color: 'var(--po-danger)' }}>Delete Project</label>
                  <div style={descStyle}>Permanently remove this project and all its data.</div>
                </div>
                <button
                  onClick={() => setDeleteDialogOpen(true)}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: 6,
                    height: 30,
                    padding: '0 12px',
                    background: 'color-mix(in srgb, var(--po-danger) 14%, transparent)',
                    border: '1px solid color-mix(in srgb, var(--po-danger) 34%, transparent)',
                    borderRadius: 6,
                    color: 'var(--po-danger)',
                    fontSize: 12,
                    fontWeight: 500,
                    fontFamily: T.fontSans,
                    cursor: 'pointer',
                    transition: `background 0.15s ${T.ease}, color 0.15s ${T.ease}, border-color 0.15s ${T.ease}`,
                  }}
                  onMouseEnter={e => {
                    e.currentTarget.style.background = 'color-mix(in srgb, var(--po-danger) 20%, transparent)';
                    e.currentTarget.style.borderColor = 'color-mix(in srgb, var(--po-danger) 45%, transparent)';
                    e.currentTarget.style.color = 'var(--po-danger)';
                  }}
                  onMouseLeave={e => {
                    e.currentTarget.style.background = 'color-mix(in srgb, var(--po-danger) 12%, transparent)';
                    e.currentTarget.style.borderColor = 'color-mix(in srgb, var(--po-danger) 32%, transparent)';
                    e.currentTarget.style.color = 'var(--po-danger)';
                  }}
                >
                  Delete Project
                </button>
              </div>
            </div>
          </div>

        </div>
      </div>

      {editDialogOpen && (
        <ProjectManageDialog layer="modalNested" mode="edit" projectId={currentProject.id} projects={scopedProjects} onClose={() => { setEditDialogOpen(false); refreshProjects(currentOrg?.id ?? null); }} />
      )}
      {deleteDialogOpen && (
        <ProjectManageDialog
          mode="delete"
          layer="modalNested"
          projectId={currentProject.id}
          projects={scopedProjects}
          onClose={() => setDeleteDialogOpen(false)}
          onDeleted={() => router.push('/home')}
        />
      )}
      <ConfirmDialog
        layer="modalNested"
        open={memberRemoval !== null}
        title={memberRemoval ? `Remove ${memberRemoval.name}?` : 'Remove member?'}
        description="This removes the member from this project. They may still have access through the organization if the project is organization-visible."
        confirmLabel="Remove"
        loading={memberRemovalLoading}
        onCancel={() => {
          if (!memberRemovalLoading) setMemberRemoval(null);
        }}
        onConfirm={() => void handleConfirmRemoveMember()}
      />
    </div>
    </ProjectSettingsDialogFrame>
  );
}

// ─── Style primitives ────────────────────────────────────────────────
//
// All of these derive from `T` so the page reads as the same family
// as Access / Monitor / History. The earlier set hardcoded neutrals
// at low alpha (`var(--po-inset)`, `var(--po-overlay)`, `var(--po-filetree-rail)`) and a 4px box-shadow
// on every card, which gave the page a "settings-form 2018" feel
// against the rest of the chrome. The replacements use translucent
// borders + a subtle `var(--po-panel)` lift instead.
//
// Section title is 10.5px / 600 / `T.text3` / uppercase 0.08em — the
// exact spec used by `SectionLabel` on the Access page, so the two
// surfaces are interchangeable in the eye.

const sectionTitle: CSSProperties = {
  fontSize: 10.5,
  fontWeight: 600,
  color: T.text3,
  fontFamily: T.fontSans,
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
  marginBottom: 12,
  paddingLeft: 2,
};

const cardBox: CSSProperties = {
  border: `1px solid ${T.cardBorder}`,
  borderRadius: 8,
  overflow: 'hidden',
  background: T.cardBg,
};

const row: CSSProperties = {
  padding: '20px',
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  gap: 16,
};

const labelStyle: CSSProperties = {
  display: 'block',
  fontSize: 13,
  fontWeight: 500,
  color: 'var(--po-text)',
  marginBottom: 4,
  fontFamily: T.fontSans,
};

const descStyle: CSSProperties = {
  fontSize: 12,
  color: T.text2,
  lineHeight: 1.55,
  fontFamily: T.fontSans,
};

// Ghost button — pulled directly from the Access page's `GhostButton`
// shape (30px tall, 12px text, transparent → 0.05-alpha hover). One
// neutral button across the whole project surface.
const btnGhost: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 6,
  height: 30,
  padding: '0 10px',
  background: 'transparent',
  border: `1px solid ${T.border}`,
  borderRadius: 6,
  color: T.text2,
  fontSize: 12,
  fontWeight: 500,
  fontFamily: T.fontSans,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  transition: `background 0.15s ${T.ease}, color 0.15s ${T.ease}, border-color 0.15s ${T.ease}`,
};

// Primary action — same shape as the ghost, just with a permanently
// raised background so it reads as the dominant action in its row.
// Avoids the heavy "white pill on black" CTA the page used before,
// which felt out of step with the muted surface.
const btnPrimary: CSSProperties = {
  ...btnGhost,
  background: 'var(--po-border-subtle)',
  borderColor: 'var(--po-border-strong)',
  color: T.text1,
};

const selectStyle: CSSProperties = {
  flex: 1,
  background: 'var(--po-panel)',
  border: `1px solid ${T.cardBorder}`,
  borderRadius: 6,
  padding: '6px 10px',
  color: 'var(--po-text)',
  fontSize: 12,
  fontFamily: T.fontSans,
  outline: 'none',
  transition: `border-color 0.15s ${T.ease}`,
};

// Hover handlers for the ghost / primary buttons. Inline-styled
// React buttons can't use `:hover`, so we attach matching enter/leave
// handlers on the consuming JSX. Defined once here so each call site
// picks the same hover ramp.
function onGhostEnter(e: React.MouseEvent<HTMLButtonElement>) {
  e.currentTarget.style.background = 'var(--po-hover)';
  e.currentTarget.style.borderColor = 'var(--po-border-strong)';
  e.currentTarget.style.color = T.text1;
}
function onGhostLeave(e: React.MouseEvent<HTMLButtonElement>) {
  e.currentTarget.style.background = 'transparent';
  e.currentTarget.style.borderColor = T.border;
  e.currentTarget.style.color = T.text2;
}

function onPrimaryEnter(e: React.MouseEvent<HTMLButtonElement>) {
  e.currentTarget.style.background = 'var(--po-border-strong)';
  e.currentTarget.style.borderColor = 'var(--po-border-strong)';
}
function onPrimaryLeave(e: React.MouseEvent<HTMLButtonElement>) {
  e.currentTarget.style.background = 'var(--po-border-subtle)';
  e.currentTarget.style.borderColor = 'var(--po-border-strong)';
}
