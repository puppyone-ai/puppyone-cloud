'use client';

import React, { useState, useEffect, useMemo, memo } from 'react';
import { useSelectedLayoutSegments } from 'next/navigation';
import dynamic from 'next/dynamic';
import { ProjectNavigationRegion } from '@/components/sidebar/ProjectNavigationRegion';
import { useProject, useProjects } from '@/lib/hooks/useData';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import {
  OrganizationProvider,
  useOrganization,
} from '@/contexts/OrganizationContext';
import { OnboardingProvider } from '@/contexts/OnboardingContext';
import { rememberLastProject } from '@/lib/lastProject';
import { useOnboarding } from '@/lib/hooks/useOnboarding';
import { ActivityStack } from '@/components/ActivityStack';
import { WorkspaceShellProvider } from '@/contexts/WorkspaceShellContext';
import { ResponsiveWorkspaceProvider, useWorkspaceActions } from '@/features/workspace/responsive';
import { useWorkspaceLayoutStore } from '@/features/workspace/layoutStore';
import { WorkspaceLayoutFrame } from '@/features/workspace/WorkspaceLayoutFrame';
import { useWorkspaceRegions } from '@/features/workspace/regions';
import { WorkspaceNavigationButton } from '@/components/sidebar/WorkspaceNavigationButton';
import '@/app/responsive-workspace.css';
import { EditorSessionProvider } from '@/features/files/editor/EditorSessionProvider';
import { ExplorerSessionsProvider } from '@/features/files/explorerSession';
import { WorkspaceNavigationProvider } from '@/features/workspace/navigation';

// Lazy-loaded — don't affect the initial app shell bundle
const WelcomeModal = dynamic(
  () =>
    import('@/components/onboarding/WelcomeModal').then(m => ({
      default: m.WelcomeModal,
    })),
  { ssr: false }
);

const MainLayoutInner = memo(function MainLayoutInner({
  children,
}: {
  children: React.ReactNode;
}) {
  const selectedSegments = useSelectedLayoutSegments();
  const segments = useMemo(
    () => (selectedSegments ?? []).filter(segment => !segment.startsWith('(')),
    [selectedSegments]
  );
  const { session, isAuthReady } = useAuth();
  const { currentOrg, switchOrg, isLoading: orgsLoading, error: orgsError, refreshOrgs } = useOrganization();

  const activeBaseId = useMemo(() => {
    return segments[0] === 'projects' && segments[1] ? segments[1] : '';
  }, [segments]);

  const { project: routeProject } = useProject(
    session ? activeBaseId || null : null
  );
  // Deep-linked project metadata can identify the org before the org list
  // arrives. Its files and the navigation list are independent read lanes.
  const { projects, isLoading: projectsLoading, error: projectsError, refresh: refreshProjectList } = useProjects(
    session ? routeProject?.org_id ?? currentOrg?.id ?? null : null
  );

  useEffect(() => {
    const routeOrgId = routeProject?.org_id;
    if (!routeOrgId || currentOrg?.id === routeOrgId) return;
    switchOrg(routeOrgId);
  }, [currentOrg?.id, routeProject?.org_id, switchOrg]);

  useEffect(() => {
    if (activeBaseId) rememberLastProject(activeBaseId);
  }, [activeBaseId]);

  // Never masquerade a route detail as a complete navigation snapshot.
  const navigationLoading = !isAuthReady || !session || orgsLoading || projectsLoading;
  const navigationError = orgsError ?? projectsError;

  const { closeNavigation, setProjectsOpen } = useWorkspaceActions();
  const regions = useWorkspaceRegions();
  useEffect(() => {
    closeNavigation();
  }, [activeBaseId, closeNavigation]);

  const [isNavCollapsed, setIsNavCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(220);
  const layoutStore = useWorkspaceLayoutStore();
  const workspaceShell = useMemo(
    () => ({
      isProjectRailCollapsed: isNavCollapsed,
      toggleProjectRail: () => layoutStore.getState().layout.projects.presentation === 'overlay' ? setProjectsOpen(false) : setIsNavCollapsed(collapsed => !collapsed),
    }),
    [isNavCollapsed, setProjectsOpen, layoutStore]
  );

  // Onboarding lives at shell level so first-run state survives project navigation.
  const onboarding = useOnboarding();

  const userIdentityLoading = !isAuthReady || !session;

  const userInitial = userIdentityLoading
    ? ''
    : (session?.user?.email?.[0] || 'U').toUpperCase();
  const userMetadata = session?.user?.user_metadata as
    Record<string, unknown> | undefined;
  const userAvatarUrl =
    (userMetadata?.avatar_url as string) ||
    (userMetadata?.picture as string) ||
    (userMetadata?.avatarUrl as string) ||
    undefined;
  return (
    <WorkspaceShellProvider {...workspaceShell}>
      <WorkspaceLayoutFrame>
        {/* Welcome modal — first-ever visit */}
        {!onboarding.hasSeenWelcome && (
          <WelcomeModal onDone={onboarding.completeWelcome} />
        )}

        <ActivityStack projectId={activeBaseId || undefined} />

        <ProjectNavigationRegion
          projects={projects}
          projectsLoading={navigationLoading}
          projectsError={navigationError}
          onRetryProjects={() => { void (orgsError ? refreshOrgs() : refreshProjectList()).catch(() => {}); }}
          activeProjectId={activeBaseId}
          userInitial={userInitial}
          userAvatarUrl={userAvatarUrl}
          isCollapsed={isNavCollapsed}
          sidebarWidth={sidebarWidth}
          onSidebarWidthChange={setSidebarWidth}
          userIdentityLoading={userIdentityLoading}
        />

        <main
          className='workspace-main'
          ref={regions.appContent}
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            minWidth: 0,
            overflow: 'hidden',
            background: 'var(--po-canvas)',
          }}
        >
          {!activeBaseId && <div className='workspace-mobile-heading'><WorkspaceNavigationButton /><span>PuppyOne</span></div>}
          {children}
        </main>
      </WorkspaceLayoutFrame>
    </WorkspaceShellProvider>
  );
});

export default function MainLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const { userId } = useAuth();
  return (
    <EditorSessionProvider key={userId ?? 'anonymous'} scope={userId ?? 'anonymous'}><ExplorerSessionsProvider><OrganizationProvider>
      <OnboardingProvider>
        <WorkspaceNavigationProvider><ResponsiveWorkspaceProvider><MainLayoutInner>{children}</MainLayoutInner></ResponsiveWorkspaceProvider></WorkspaceNavigationProvider>
      </OnboardingProvider>
    </OrganizationProvider></ExplorerSessionsProvider></EditorSessionProvider>
  );
}
