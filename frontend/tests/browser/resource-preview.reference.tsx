import { useEffect, useState } from 'react';
import { ProjectsHeader } from '@/components/ProjectsHeader';
import { MarkdownEditor } from '@/components/editors/markdown';
import { DataExplorerPane } from '@/features/files/components/explorer/DataExplorerPane';
import { ExplorerSessionProvider } from '@/features/files/explorerSession';
import { ProjectSessionProvider } from '@/features/workspace/session';
import { ResponsiveWorkspaceProvider } from '@/features/workspace/responsive';
import { WorkspaceLayoutFrame } from '@/features/workspace/WorkspaceLayoutFrame';
import { createWorkspaceLayoutStore } from '@/features/workspace/layoutStore';
import { DEFAULT_WORKSPACE_INPUT } from '@/features/workspace/paneLayout';
import { projectId, readWorkspaceResource } from './resource-preview.ports';

/** Real Workspace header/explorer/editor with fake transport, used as the visual reference. */
export function WorkspaceReference() {
  const [store] = useState(() => createWorkspaceLayoutStore({ projects: { ...DEFAULT_WORKSPACE_INPUT.projects, present: false } }));
  const [text, setText] = useState('');
  useEffect(() => { void readWorkspaceResource(projectId, 'Welcome.md').then(value => setText(value.content_text!)); }, []);
  return <ResponsiveWorkspaceProvider layoutStore={store}><WorkspaceLayoutFrame>
    <ProjectSessionProvider projectId={projectId}><ExplorerSessionProvider projectId={projectId}>
      <main className="workspace-project-layout">
        <ProjectsHeader projectId={projectId} pathSegments={[{ label: 'Research workspace' }]} />
        <div className="workspace-primary-body" style={{ display: 'flex', minHeight: 0, minWidth: 0 }}>
          <DataExplorerPane projectId={projectId} folderBreadcrumbs={[]} activeNodeId="Welcome.md" onNavigate={() => {}} />
          <section style={{ display: 'flex', flex: 1, minHeight: 0, minWidth: 0 }}>
            <MarkdownEditor content={text} documentKey="Welcome.md" viewMode="wysiwyg" onChange={() => {}} />
          </section>
        </div>
      </main>
    </ExplorerSessionProvider></ProjectSessionProvider>
  </WorkspaceLayoutFrame></ResponsiveWorkspaceProvider>;
}
