'use client';

import { FolderTree } from 'lucide-react';
import { useWorkspaceActions, useWorkspaceNavigation } from '@/features/workspace/responsive';

export function WorkspaceFilesButton() {
  const open = useWorkspaceNavigation(state => state.filesOpen);
  const { setFilesOpen } = useWorkspaceActions();
  return <button type='button' className='workspace-files-button' aria-label='Browse files' title='Browse files' aria-expanded={open} aria-controls='workspace-files' onClick={() => setFilesOpen(!open)}><FolderTree size={19} /></button>;
}
