'use client';

import React, { use } from 'react';
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

  return (
    <AgentProvider key={projectId} projectId={projectId}>
      <ProjectSessionProvider projectId={projectId}>
        <VersionWebSocketProvider projectId={projectId}>
          <ProjectLayoutInner projectId={projectId}>{children}</ProjectLayoutInner>
        </VersionWebSocketProvider>
      </ProjectSessionProvider>
    </AgentProvider>
  );
}
