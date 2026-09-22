'use client';

import { use, type ReactNode } from 'react';
import { FileWorkspaceQueriesProvider } from '@/features/files/FileWorkspaceQueriesProvider';
import { FilesWorkspace } from '@/features/files/FilesWorkspace';

/** File-path navigation updates the outlet identity, not the tree's lifetime. */
export default function FilesLayout({ params, children }: {
  params: Promise<{ projectId: string }>;
  children: ReactNode;
}) {
  const { projectId } = use(params);
  return <FileWorkspaceQueriesProvider projectId={projectId}>
    <FilesWorkspace projectId={projectId} />
    {children}
  </FileWorkspaceQueriesProvider>;
}
