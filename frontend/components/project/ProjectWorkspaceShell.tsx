'use client';

import {
  createContext,
  lazy,
  Suspense,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { useSelectedLayoutSegment } from 'next/navigation';
import {
  ProjectHeaderBreadcrumbs,
  ProjectsHeader,
  type BreadcrumbSegment,
} from '@/components/ProjectsHeader';
import {
  DesktopChromeDivider,
} from '@/components/chrome/DesktopChrome';
import { ProjectAuxiliaryActions } from './ProjectAuxiliaryActions';
import { ProjectSettingsDialogFrame } from './ProjectSettingsDialogFrame';
import { PageLoading } from '@/components/loading';
import { useWorkspaceRegions } from '@/features/workspace/regions';

const ProjectSettingsDialog = lazy(() => import('./ProjectSettingsDialog'));

type HeaderTargets = {
  openSettings: () => void;
  breadcrumbs: HTMLDivElement | null;
  actions: HTMLDivElement | null;
};

const ProjectHeaderTargetsContext = createContext<HeaderTargets | null>(null);

const WORKSPACE_SEGMENTS = new Set(['data', 'history', 'changes', 'access']);

/**
 * Owns the stable project header and the body viewport beneath it.
 * Route pages contribute only page-specific breadcrumbs/actions through
 * portals; Files/Git navigation and project utilities never remount.
 */
export function ProjectWorkspaceShell({
  projectId,
  children,
  auxiliary,
}: {
  readonly auxiliary?: ReactNode;
  readonly projectId: string;
  readonly children: ReactNode;
}) {
  const segment = useSelectedLayoutSegment();
  const regions = useWorkspaceRegions();
  const [breadcrumbs, setBreadcrumbs] = useState<HTMLDivElement | null>(null);
  const [actions, setActions] = useState<HTMLDivElement | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const openSettings = useCallback(() => setSettingsOpen(true), []);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);
  const targets = useMemo(() => ({ openSettings, breadcrumbs, actions }), [openSettings, actions, breadcrumbs]);
  const workspaceHeaderVisible = Boolean(segment && WORKSPACE_SEGMENTS.has(segment));
  const activeView = segment === 'history' || segment === 'changes' ? 'git' : 'files';

  return (
    <ProjectHeaderTargetsContext.Provider value={targets}>
      <div className='workspace-project-layout'>
        {workspaceHeaderVisible && (
          <ProjectsHeader
            projectId={projectId}
            activeView={activeView}
            pathContent={(
              <div
                ref={setBreadcrumbs}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  flex: '0 1 auto',
                  minWidth: 0,
                  overflow: 'visible',
                }}
              />
            )}
            actionSlot={(
              <div className='workspace-header-utilities' style={{ display: 'flex', alignItems: 'center', gap: 3, height: 24 }}>
                <div ref={setActions} style={{ display: 'contents' }} />
                <DesktopChromeDivider />
                <ProjectAuxiliaryActions />
              </div>
            )}
          />
        )}

        <div
          className='workspace-primary-body'
          ref={regions.projectBody}
          style={{
            flex: 1,
            minWidth: 0,
            minHeight: 0,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          {children}
        </div>
        {auxiliary}
      </div>
      {settingsOpen && (
        <Suspense fallback={(
          <ProjectSettingsDialogFrame onClose={closeSettings}>
            <PageLoading variant='fill' />
          </ProjectSettingsDialogFrame>
        )}>
          <ProjectSettingsDialog projectId={projectId} onClose={closeSettings} />
        </Suspense>
      )}
    </ProjectHeaderTargetsContext.Provider>
  );
}

/** Page-owned content rendered into the project-layout-owned header. */
export function ProjectHeaderContribution({
  pathSegments,
  actions,
  canManageSettings = false,
}: {
  readonly pathSegments: BreadcrumbSegment[];
  readonly actions?: ReactNode;
  readonly canManageSettings?: boolean;
}) {
  const targets = useContext(ProjectHeaderTargetsContext);

  if (!targets) {
    throw new Error('ProjectHeaderContribution requires ProjectWorkspaceShell');
  }

  return (
    <>
      {targets.breadcrumbs
        ? createPortal(
            <ProjectHeaderBreadcrumbs
              pathSegments={pathSegments}
              onOpenSettings={canManageSettings ? targets.openSettings : undefined}
            />,
            targets.breadcrumbs,
          )
        : null}
      {targets.actions && actions
        ? createPortal(actions, targets.actions)
        : null}
    </>
  );
}

/** Compatibility routes can open settings over the persistent workspace. */
export function useOpenProjectSettings() {
  const targets = useContext(ProjectHeaderTargetsContext);
  if (!targets) throw new Error('Project settings require ProjectWorkspaceShell');
  return targets.openSettings;
}
