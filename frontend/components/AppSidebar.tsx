'use client';

import React, { memo } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { GitPullRequestArrow, LayoutTemplate } from 'lucide-react';
import useSWR from 'swr';
import { projectAllows, type ProjectInfo } from '../lib/projectsApi';
import type { OrganizationInfo } from '../lib/organizationsApi';
import { getProjectHistory } from '../lib/contentTreeApi';
import { PROJECT_LOGS_ENABLED } from '../lib/featureFlags';
import { listKinds } from '../lib/needsActionRegistry';
import { isSnoozed } from '../lib/needsActionSnooze';
import { SidebarLayout, type NavItem } from './sidebar/SidebarLayout';

// Side-effect import: populate the same Needs Action registry used by
// the Changes page before the sidebar summary reads it.
import '@/features/history/components/items';

type AppSidebarProps = {
  projects: ProjectInfo[];
  projectsLoading?: boolean;
  activeBaseId: string;
  activeView?: string;
  userInitial: string;
  userAvatarUrl?: string;
  environmentLabel?: string;
  isCollapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
  sidebarWidth?: number;
  onSidebarWidthChange?: (width: number) => void;
  currentOrg?: OrganizationInfo | null;
  organizations?: ReadonlyArray<{ id: string; name: string }>;
  onSwitchOrg?: (orgId: string) => void;
  organizationIdentityLoading?: boolean;
  projectIdentityLoading?: boolean;
  userIdentityLoading?: boolean;
  onOpenGuide?: () => void;
};

type NeedsActionSidebarSummary = {
  count: number;
  hasErrors: boolean;
};

async function getNeedsActionSidebarSummary(projectId: string): Promise<NeedsActionSidebarSummary> {
  const results = await Promise.allSettled(
    listKinds().map(async (def) => {
      const items = await def.fetchItems(projectId);
      return items.filter(
        (item) => !isSnoozed({ projectId, kind: def.kind, id: item.id }),
      ).length;
    }),
  );

  return results.reduce<NeedsActionSidebarSummary>(
    (summary, result) => {
      if (result.status === 'fulfilled') {
        summary.count += result.value;
      } else {
        summary.hasErrors = true;
      }
      return summary;
    },
    { count: 0, hasErrors: false },
  );
}

export const AppSidebar = memo(function AppSidebar({
  projects,
  projectsLoading = false,
  activeBaseId,
  activeView = 'projects',
  userInitial,
  userAvatarUrl,
  environmentLabel,
  isCollapsed = false,
  onCollapsedChange,
  sidebarWidth,
  onSidebarWidthChange,
  currentOrg,
  organizations,
  onSwitchOrg,
  organizationIdentityLoading = false,
  projectIdentityLoading = false,
  userIdentityLoading = false,
  onOpenGuide,
}: AppSidebarProps) {
  const router = useRouter();
  const t = useTranslations('nav');

  const activeProjectFromList = activeBaseId
    ? projects.find(p => p.id === activeBaseId)
    : null;
  const activeProject = activeBaseId
    ? activeProjectFromList ?? {
        id: activeBaseId,
        name: '',
      }
    : null;
  const activeProjectTitleLoading =
    Boolean(activeBaseId && !activeProjectFromList) || projectIdentityLoading;
  const activeProjectId = activeProject?.id ?? null;

  const projectOptions = projects.map((p) => ({
    id: p.id,
    name: p.name,
  }));

  // Lightweight stats for the footer chip — `limit=1` keeps payload
  // small while `total` still reflects the real commit count.
  // Cached for 60s to avoid refetching every render.
  const { data: history } = useSWR(
    activeProjectFromList ? ['sidebar-stats', activeProjectFromList.id] : null,
    () => getProjectHistory(activeProjectFromList!.id, 1),
    { revalidateOnFocus: false, dedupingInterval: 60000 },
  );

  // Needs Action count — drives the badge on Changes. Use the same
  // registry as the Changes page so failed syncs / risky deletes /
  // pending reviews all surface at the app-navigation entry point.
  const { data: needsActionSummary, isLoading: needsActionLoading } = useSWR(
    activeProjectId ? ['sidebar-needs-action', activeProjectId] : null,
    () => getNeedsActionSidebarSummary(activeProjectId!),
    { refreshInterval: 30_000, dedupingInterval: 15_000, revalidateOnFocus: true },
  );
  const needsActionCount = needsActionSummary?.count ?? 0;

  const projectStats = activeProjectFromList
    ? {
        commitCount: history?.total,
      }
    : undefined;

  if (activeProject) {
    const projectNavItems: NavItem[] = [
      {
        id: 'data',
        label: t('context'),
        icon: (
          <svg width='15' height='15' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2' strokeLinecap='round' strokeLinejoin='round'>
            <path d='M4 7c0-1.1.9-2 2-2h4l2 2h6a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7Z' />
          </svg>
        ),
      },
      {
        id: 'changes',
        label: t('changes'),
        icon: <GitPullRequestArrow size={15} strokeWidth={2} />,
        badge: needsActionCount > 0 ? needsActionCount : undefined,
        badgeLoading: needsActionLoading,
        badgeTone: 'danger',
      },
      {
        id: 'access',
        // Chain-link glyph — diagonal / 45°-rotated variant of the
        // Lucide `link` icon. The earlier horizontal version only
        // filled ~20×10 of the 24-grid which made the strokes read as
        // thin and the icon as "too wide" next to its siblings (folder
        // / clock / code / gear all fill ~18×18). The rotated path
        // shares the same square footprint as those four, so the rail
        // reads as one consistent family.
        label: t('access'),
        icon: (
          <svg width='15' height='15' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2' strokeLinecap='round' strokeLinejoin='round'>
            <path d='M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71' />
            <path d='M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71' />
          </svg>
        ),
        groupEnd: true,
      },
      {
        id: 'integrations',
        label: t('integrations'),
        icon: (
          <svg width='15' height='15' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2' strokeLinecap='round' strokeLinejoin='round'>
            <rect x='3' y='3' width='7' height='7' rx='1.5' />
            <rect x='14' y='3' width='7' height='7' rx='1.5' />
            <rect x='3' y='14' width='7' height='7' rx='1.5' />
            <rect x='14' y='14' width='7' height='7' rx='1.5' />
            <path d='M10 6.5h4' />
            <path d='M6.5 10v4' />
            <path d='M10 17.5h4' />
            <path d='M17.5 10v4' />
          </svg>
        ),
      },
      ...(PROJECT_LOGS_ENABLED
        ? [{
            id: 'develop',
            label: t('develop'),
            icon: (
              <svg width='15' height='15' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2' strokeLinecap='round' strokeLinejoin='round'>
                <polyline points='16 18 22 12 16 6' />
                <polyline points='8 6 2 12 8 18' />
                <line x1='14' y1='4' x2='10' y2='20' />
              </svg>
            ),
          }]
        : []),
      // HIDDEN: Toolkit nav item temporarily disabled
      // {
      //   id: 'toolkit',
      //   label: 'Toolkit',
      //   icon: (
      //     <svg width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='1.5' strokeLinecap='round' strokeLinejoin='round'>
      //       <path d='M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z' />
      //     </svg>
      //   ),
      // },
      ...(projectAllows(activeProjectFromList, 'project.settings.manage') ? [{
        id: 'settings',
        label: t('settings'),
        icon: (
          <svg width='15' height='15' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2' strokeLinecap='round' strokeLinejoin='round'>
            <circle cx='12' cy='12' r='3' />
            <path d='M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 0 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 0 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 0 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3h.1a1.6 1.6 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 0 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8v.1a1.6 1.6 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1Z' />
          </svg>
        ),
      }] : []),
    ];

    return (
      <SidebarLayout
        title={activeProject.name}
        titleLoading={activeProjectTitleLoading}
        context="project"
        currentProjectId={activeProject.id}
        projects={projectOptions}
        projectsLoading={projectsLoading}
        onSelectProject={(projectId) => router.push(`/projects/${projectId}/data`)}
        onHoverProject={(projectId) => {
          router.prefetch(`/projects/${projectId}/data`);
        }}
        onGoHome={() => router.push('/home')}
        activeView={activeView}
        navItems={projectNavItems}
        onNavigate={(viewId) => {
          if (viewId === 'projects') {
            router.push(`/projects/${activeProject.id}/data`);
          } else if (viewId === 'data') {
            router.push(`/projects/${activeProject.id}/data`);
          } else if (viewId === 'changes') {
            router.push(`/projects/${activeProject.id}/changes`);
          } else if (viewId === 'access') {
            router.push(`/projects/${activeProject.id}/access`);
          } else if (viewId === 'integrations') {
            router.push(`/projects/${activeProject.id}/workflows`);
          } else if (viewId === 'history') {
            router.push(`/projects/${activeProject.id}/history`);
          } else if (viewId === 'develop') {
            router.push(`/projects/${activeProject.id}/develop/logs`);
          } else if (viewId === 'toolkit') {
            router.push(`/projects/${activeProject.id}/toolkit`);
          } else if (viewId === 'settings') {
            router.push(`/projects/${activeProject.id}/settings`);
          }
        }}
        onHoverNavItem={(viewId) => {
          const id = activeProject.id;
          const pathMap: Record<string, string> = {
            data: `/projects/${id}/data`,
            changes: `/projects/${id}/changes`,
            access: `/projects/${id}/access`,
            integrations: `/projects/${id}/workflows`,
            history: `/projects/${id}/history`,
            develop: `/projects/${id}/develop/logs`,
            toolkit: `/projects/${id}/toolkit`,
            settings: `/projects/${id}/settings`,
          };
          if (pathMap[viewId]) router.prefetch(pathMap[viewId]);
        }}
        onBack={() => router.push('/home')}
        userInitial={userInitial}
        userAvatarUrl={userAvatarUrl}
        userIdentityLoading={userIdentityLoading}
        environmentLabel={environmentLabel}
        isCollapsed={isCollapsed}
        onCollapsedChange={onCollapsedChange}
        sidebarWidth={sidebarWidth}
        onSidebarWidthChange={onSidebarWidthChange}
        onOpenGuide={onOpenGuide}
        projectStats={projectStats}
      />
    );
  }

  const globalNavItems: NavItem[] = [
    {
      id: 'home',
      label: t('home'),
      icon: (
        <svg width='15' height='15' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2' strokeLinecap='round' strokeLinejoin='round'>
          <path d='M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z' />
          <polyline points='3.27 6.96 12 12.01 20.73 6.96' />
          <line x1='12' y1='22.08' x2='12' y2='12' />
        </svg>
      ),
      badge: projects.length,
      badgeLoading: projectsLoading,
    },
    {
      id: 'templates',
      label: t('templates'),
      icon: <LayoutTemplate size={15} strokeWidth={2} />,
    },
    {
      id: 'team',
      label: t('team'),
      icon: (
        <svg width='15' height='15' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2' strokeLinecap='round' strokeLinejoin='round'>
          <path d='M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2' />
          <circle cx='9' cy='7' r='4' />
          <path d='M23 21v-2a4 4 0 0 0-3-3.87' />
          <path d='M16 3.13a4 4 0 0 1 0 7.75' />
        </svg>
      ),
    },
    {
      id: 'billing',
      label: t('billing'),
      icon: (
        <svg width='15' height='15' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2' strokeLinecap='round' strokeLinejoin='round'>
          <rect x='1' y='4' width='22' height='16' rx='2' ry='2' />
          <line x1='1' y1='10' x2='23' y2='10' />
        </svg>
      ),
    },
  ];

  return (
    <SidebarLayout
      title={currentOrg?.name ?? 'puppyone'}
      titleLoading={organizationIdentityLoading}
      context="global"
      currentProjectId={null}
      projects={projectOptions}
      projectsLoading={projectsLoading}
      onSelectProject={(projectId) => router.push(`/projects/${projectId}/data`)}
      onGoHome={() => router.push('/home')}
      organizations={organizations}
      currentOrgId={currentOrg?.id ?? null}
      onSwitchOrg={onSwitchOrg}
      activeView={activeView}
      navItems={globalNavItems}
      onNavigate={(viewId) => {
        if (viewId === 'home') {
          router.push('/home');
        } else if (viewId === 'templates') {
          router.push('/templates');
        } else if (viewId === 'team') {
          router.push('/team');
        } else if (viewId === 'billing') {
          router.push('/billing');
        }
      }}
      userInitial={userInitial}
      userAvatarUrl={userAvatarUrl}
      userIdentityLoading={userIdentityLoading}
      environmentLabel={environmentLabel}
      isCollapsed={isCollapsed}
      onCollapsedChange={onCollapsedChange}
      sidebarWidth={sidebarWidth}
      onSidebarWidthChange={onSidebarWidthChange}
      onOpenGuide={onOpenGuide}
    />
  );
});
