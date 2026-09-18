'use client';

import React, {
  useCallback,
  useEffect,
  Suspense,
  useRef,
  useState,
} from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { refreshProjects, useProjects } from '@/lib/hooks/useData';
import { useAuth } from '@/app/supabase/SupabaseAuthProvider';
import { useOrganization } from '@/contexts/OrganizationContext';
import {
  DashboardLoadError,
  DashboardLoadingSkeleton,
  DashboardView,
} from '@/components/dashboard/DashboardView';
import { useOnboarding } from '@/lib/hooks/useOnboarding';
import { nextUntitledProjectName } from '@/lib/projectNames';
import { createProject } from '@/lib/projectsApi';

function DashboardPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isAuthReady } = useAuth();
  const {
    orgs,
    currentOrg,
    isLoading: orgsLoading,
    error: orgsError,
    refreshOrgs,
  } = useOrganization();
  const {
    projects,
    isLoading: projectsLoading,
    error: projectsError,
    refresh: refreshProjectList,
  } = useProjects(currentOrg?.id ?? null);
  const [isCreatingProject, setIsCreatingProject] = useState(false);
  const [isRetryingLoad, setIsRetryingLoad] = useState(false);
  const creatingProjectRef = useRef(false);
  const createOperationRef = useRef<{
    idempotencyKey: string;
    name: string;
    orgId: string;
  } | null>(null);
  const handledCreateParamRef = useRef(false);

  // Auto-complete 'project' onboarding step when user has a project
  const { completeStep } = useOnboarding();
  useEffect(() => {
    if (!projectsLoading && projects.length > 0) {
      completeStep('project');
    }
  }, [projects, projectsLoading]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleCreateProject = useCallback(async () => {
    if (creatingProjectRef.current) return;

    creatingProjectRef.current = true;
    setIsCreatingProject(true);

    try {
      if (!currentOrg?.id) {
        throw new Error('Select an organization before creating a project.');
      }
      const projectName = nextUntitledProjectName(projects);
      const previousOperation = createOperationRef.current;
      const operation =
        previousOperation?.name === projectName &&
        previousOperation.orgId === currentOrg.id
          ? previousOperation
          : {
              idempotencyKey: crypto.randomUUID(),
              name: projectName,
              orgId: currentOrg.id,
            };
      createOperationRef.current = operation;
      const created = await createProject({
        name: projectName,
        description: '',
        orgId: currentOrg.id,
        idempotencyKey: operation.idempotencyKey,
      });
      createOperationRef.current = null;
      completeStep('project');
      router.push(`/projects/${created.id}/data`);
      void refreshProjects(currentOrg?.id);
    } catch (error) {
      creatingProjectRef.current = false;
      setIsCreatingProject(false);
      console.error('Failed to create project:', error);
      alert(
        'Create project failed: ' +
          (error instanceof Error ? error.message : 'Unknown error')
      );
    }
  }, [completeStep, currentOrg?.id, projects, router]);

  // Handle ?create=true query param
  useEffect(() => {
    if (
      searchParams?.get('create') === 'true' &&
      !projectsLoading &&
      !creatingProjectRef.current &&
      !handledCreateParamRef.current
    ) {
      handledCreateParamRef.current = true;
      router.replace('/home');
      void handleCreateProject();
    }
  }, [searchParams, projectsLoading, router, handleCreateProject]);

  const initialLoadError =
    (orgs.length === 0 ? orgsError : null) ??
    (projects.length === 0 ? projectsError : null);

  const handleRetryLoad = useCallback(async () => {
    if (isRetryingLoad) return;
    setIsRetryingLoad(true);
    try {
      if (orgs.length === 0 && orgsError) {
        await refreshOrgs();
      } else {
        await refreshProjectList();
      }
    } finally {
      setIsRetryingLoad(false);
    }
  }, [isRetryingLoad, orgs.length, orgsError, refreshOrgs, refreshProjectList]);

  if (initialLoadError) {
    return (
      <DashboardLoadError
        retrying={isRetryingLoad}
        onRetry={() => void handleRetryLoad()}
      />
    );
  }

  if (
    !isAuthReady ||
    orgsLoading ||
    (orgs.length > 0 && !currentOrg) ||
    projectsLoading
  ) {
    return <DashboardLoadingSkeleton />;
  }

  return (
    <div
      style={{
        display: 'flex',
        width: '100%',
        height: '100%',
        backgroundColor: 'var(--po-canvas)',
      }}
    >
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          margin: 0,
          borderRadius: 0,
          border: 'none',
          background: 'var(--po-canvas)',
          overflow: 'hidden',
        }}
      >
        <DashboardView
          projects={projects}
          onProjectClick={projectId => {
            router.push(`/projects/${projectId}/data`);
          }}
          onCreateClick={handleCreateProject}
          creatingProject={isCreatingProject}
        />
      </div>
    </div>
  );
}

export default function DashboardPage() {
  return (
    <Suspense fallback={<DashboardLoadingSkeleton />}>
      <DashboardPageContent />
    </Suspense>
  );
}
