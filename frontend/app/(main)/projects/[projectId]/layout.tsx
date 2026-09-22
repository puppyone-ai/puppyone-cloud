'use client';

import React, { use } from 'react';
import { ExplorerSessionProvider } from '@/features/files/explorerSession';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import { AgentProvider } from '@/contexts/AgentContext';
import { VersionWebSocketProvider } from '@/contexts/VersionWebSocketContext';
import { ProjectSessionProvider } from '@/features/workspace/session';
import { useProjectInvalidation } from '@/features/workspace/useProjectInvalidation';
import { ProjectAuxiliarySidebar } from './_components/ProjectAuxiliarySidebar';
import { ProjectWorkspaceShell } from '@/components/project/ProjectWorkspaceShell';


function ProjectLayoutInner({ children, projectId }: { children: React.ReactNode; projectId: string }) {
  useProjectInvalidation(projectId);
  return (
    <ProjectWorkspaceShell projectId={projectId} auxiliary={<ProjectAuxiliarySidebar projectId={projectId} />}>
      {children}
    </ProjectWorkspaceShell>
  );
}

interface ProjectLayoutProps {
  children: React.ReactNode;
  params: Promise<{ projectId: string }>;
}

export default function ProjectLayout({
  children,
  params,
}: ProjectLayoutProps) {
  const { projectId } = use(params);
  const { userId } = useAuth();

  return (
    <AgentProvider key={`${userId}:${projectId}`} projectId={projectId}>
      <ProjectSessionProvider projectId={projectId}><ExplorerSessionProvider projectId={projectId}>
        <VersionWebSocketProvider projectId={projectId}>
          <ProjectLayoutInner projectId={projectId}>{children}</ProjectLayoutInner>
        </VersionWebSocketProvider>
      </ExplorerSessionProvider></ProjectSessionProvider>
    </AgentProvider>
  );
}
