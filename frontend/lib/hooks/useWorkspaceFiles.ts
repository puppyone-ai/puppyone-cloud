'use client';

import {createContext, useContext} from 'react';
import {publishedFiles, type LiveWorkspace} from '@/lib/workspaceFiles';

/** Data-source contract only; the project feature owns authentication and lifetime. */
export const WorkspaceFilesContext = createContext({
  files: publishedFiles,
  identity: 'published',
  revision: 0,
  ready: true,
  workspace: null as LiveWorkspace | null,
});
export const useWorkspaceFiles = () => useContext(WorkspaceFilesContext);
