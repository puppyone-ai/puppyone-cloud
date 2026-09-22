'use client';

import { Menu } from 'lucide-react';
import { useWorkspaceActions, useWorkspaceNavigation } from '@/features/workspace/responsive';

export function WorkspaceNavigationButton() {
  const projectsOpen = useWorkspaceNavigation(state => state.projectsOpen);
  const { setProjectsOpen } = useWorkspaceActions();
  return <button type='button' className='workspace-navigation-button' aria-label='Open projects' aria-expanded={projectsOpen} aria-controls='workspace-projects' onClick={() => setProjectsOpen(true)}><Menu size={20} /></button>;
}
