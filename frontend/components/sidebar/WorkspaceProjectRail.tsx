'use client';

import clsx from 'clsx';
import { Cloud, PanelLeftClose, PanelLeftOpen, Plus } from 'lucide-react';
import { useRouter } from 'next/navigation';
import React, { memo, useCallback, useEffect, useRef, useState } from 'react';
import type { ProjectInfo } from '@/lib/projectsApi';
import { rememberLastProject } from '@/lib/lastProject';
import UserMenuPanel from '@/components/UserMenuPanel';
import { SkeletonBlock } from '@/components/loading';
import { useWorkspaceActions } from '@/features/workspace/responsive';
import { useWorkspaceShell } from '@/contexts/WorkspaceShellContext';

type WorkspaceProjectRailProps = {
  projects: ProjectInfo[];
  projectsLoading?: boolean;
  projectsError?: unknown;
  onRetryProjects?: () => void;
  activeProjectId?: string;
  userInitial: string;
  userAvatarUrl?: string;
  userIdentityLoading?: boolean;
  isCollapsed?: boolean;
  sidebarWidth?: number;
  onSidebarWidthChange?: (width: number) => void;
  maxResizeWidth?: number;
  resizable?: boolean;
};

const MIN_WIDTH = 160;
const MAX_WIDTH = 360;
const DEFAULT_WIDTH = 220;
const COLLAPSED_WIDTH = 56;

export const WorkspaceProjectRail = memo(function WorkspaceProjectRail({
  projects,
  projectsLoading = false,
  projectsError,
  onRetryProjects,
  activeProjectId,
  userInitial,
  userAvatarUrl,
  userIdentityLoading = false,
  isCollapsed = false,
  sidebarWidth = DEFAULT_WIDTH,
  onSidebarWidthChange,
  maxResizeWidth = MAX_WIDTH,
  resizable = true,
}: WorkspaceProjectRailProps) {
  const router = useRouter();
  const { setProjectsOpen } = useWorkspaceActions();
  const workspaceShell = useWorkspaceShell();
  const railRef = useRef<HTMLElement>(null);
  const [isResizing, setIsResizing] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  useEffect(() => { if (!resizable) setIsResizing(false); }, [resizable]);

  const openProject = useCallback(
    (projectId: string) => {
      setProjectsOpen(false);
      rememberLastProject(projectId);
      router.push(`/projects/${projectId}/data`);
    },
    [router, setProjectsOpen]
  );

  const startResize = useCallback((event: React.MouseEvent) => {
    event.preventDefault();
    setIsResizing(true);
  }, []);

  useEffect(() => {
    if (!isResizing) return;

    const handleMouseMove = (event: MouseEvent) => {
      if (!railRef.current) return;
      const width = Math.min(Math.max(event.clientX - railRef.current.getBoundingClientRect().left, MIN_WIDTH), maxResizeWidth);
      onSidebarWidthChange?.(width);
    };
    const handleMouseUp = () => setIsResizing(false);

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isResizing, onSidebarWidthChange, maxResizeWidth]);

  const rowClass = (active: boolean) =>
    clsx(
      'group my-px flex h-8 w-full items-center rounded-[6px] border-0 text-left transition-colors duration-150',
      isCollapsed ? 'justify-center px-0' : 'gap-1.5 px-1.5',
      active
        ? 'bg-[var(--po-hover)] text-[var(--po-text)] hover:bg-[var(--po-selected)]'
        : 'bg-transparent text-[var(--po-text-muted)] hover:bg-[var(--po-hover)] hover:text-[var(--po-text)]'
    );

  return (
    <aside
      ref={railRef}
      className={clsx(
        'workspace-project-rail relative flex h-full flex-shrink-0 flex-col font-sans',
        'transition-none'
      )}
      style={{
        width: `var(--navigation-width, ${isCollapsed ? COLLAPSED_WIDTH : sidebarWidth}px)`,
        background: 'var(--po-sidebar)',
        borderRight: '1px solid var(--po-divider)',
      }}
    >
      <div
        className={clsx(
          'flex flex-shrink-0 items-center',
          isCollapsed
            ? 'h-[76px] flex-col justify-center gap-1'
            : 'h-[46px] gap-1 px-3'
        )}
      >
        <button
          type='button'
          className='flex h-7 w-7 items-center justify-center overflow-hidden rounded-full border-0 bg-[var(--po-control)] text-[11px] font-semibold text-[var(--po-text)] transition-colors hover:bg-[var(--po-selected)]'
          onClick={() => setUserMenuOpen(true)}
          title='Account'
          aria-label='Account'
        >
          {userIdentityLoading ? (
            <SkeletonBlock width={16} height={16} radius={8} />
          ) : userAvatarUrl ? (
            <img
              src={userAvatarUrl}
              alt=''
              referrerPolicy='no-referrer'
              className='h-full w-full object-cover'
            />
          ) : (
            userInitial
          )}
        </button>

        {!isCollapsed && <span style={{ flex: 1 }} />}

        {workspaceShell && (
          <button
            type='button'
            className='flex h-7 w-7 items-center justify-center rounded-[5px] border-0 bg-transparent text-[var(--po-text-muted)] transition-colors hover:bg-[var(--po-hover)] hover:text-[var(--po-text)]'
            onClick={workspaceShell.toggleProjectRail}
            title={isCollapsed ? 'Expand projects' : 'Collapse projects'}
            aria-label={isCollapsed ? 'Expand projects' : 'Collapse projects'}
          >
            {isCollapsed ? (
              <PanelLeftOpen size={15} strokeWidth={1.8} />
            ) : (
              <PanelLeftClose size={15} strokeWidth={1.8} />
            )}
          </button>
        )}
      </div>

      <div className='flex-1 overflow-y-auto overflow-x-hidden px-3 py-2'>
        <div className='flex flex-col' aria-busy={projectsLoading} aria-label='Projects'>
          {projectsError ? (
            <div role='alert' className='px-1.5 py-2 text-xs text-[var(--po-text-muted)]'>
              {!isCollapsed && (projects.length ? 'Could not refresh projects. ' : 'Could not load projects. ')}
              <button type='button' onClick={onRetryProjects} className='underline' aria-label='Retry loading projects'>Retry</button>
            </div>
          ) : null}
          {projectsLoading && projects.length === 0 && !projectsError
            ? Array.from({ length: 5 }).map((_, index) => (
                <div
                  key={index}
                  className={clsx(
                    'my-px flex h-8 items-center',
                    isCollapsed ? 'justify-center' : 'gap-1.5 px-1.5'
                  )}
                >
                  <SkeletonBlock width={15} height={15} radius={4} />
                  {!isCollapsed && (
                    <SkeletonBlock
                      width={88 + index * 7}
                      height={11}
                      radius={3}
                    />
                  )}
                </div>
              ))
            : projects.map(project => {
                const active = project.id === activeProjectId;
                return (
                  <button
                    key={project.id}
                    type='button'
                    className={rowClass(active)}
                    onClick={() => openProject(project.id)}
                    onMouseEnter={() =>
                      router.prefetch(`/projects/${project.id}/data`)
                    }
                    title={project.name}
                    aria-current={active ? 'page' : undefined}
                  >
                    <ProjectMark name={project.name} compact={isCollapsed} />
                    {!isCollapsed && (
                      <span className='truncate text-[14px] font-normal leading-5'>
                        {project.name}
                      </span>
                    )}
                  </button>
                );
              })}
          {!projectsLoading && !projectsError && projects.length === 0 && !isCollapsed && (
            <p className='px-1.5 py-2 text-xs text-[var(--po-text-subtle)]'>No projects yet</p>
          )}

          <button
            type='button'
            className={rowClass(false)}
            onClick={() => { setProjectsOpen(false); router.push('/home?create=true'); }}
            title='Create new project'
            disabled={projectsLoading || Boolean(projectsError)}
          >
            <span className='flex h-[18px] w-[18px] flex-shrink-0 items-center justify-center'>
              <Plus size={14} strokeWidth={2.2} />
            </span>
            {!isCollapsed && (
              <span className='truncate text-[14px] font-normal leading-5'>
                Create new
              </span>
            )}
          </button>
        </div>
      </div>

      {!isCollapsed && resizable && (
        <div
          className={clsx(
            'absolute right-[-2px] top-0 z-10 h-full w-1 cursor-col-resize',
            isResizing ? 'bg-[var(--po-active)]' : 'hover:bg-[var(--po-active)]'
          )}
          onMouseDown={startResize}
          role='separator'
          aria-label='Resize projects'
          aria-orientation='vertical'
          aria-valuemin={MIN_WIDTH}
          aria-valuemax={maxResizeWidth}
          aria-valuenow={sidebarWidth}
          tabIndex={0}
          onKeyDown={event => {
            if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
            event.preventDefault();
            onSidebarWidthChange?.(Math.max(MIN_WIDTH, Math.min(maxResizeWidth, sidebarWidth + (event.key === 'ArrowRight' ? 16 : -16))));
          }}
        />
      )}

      <UserMenuPanel
        isOpen={userMenuOpen}
        onClose={() => setUserMenuOpen(false)}
        initialTab='account'
      />
    </aside>
  );
});

function ProjectMark({ name, compact }: { name: string; compact: boolean }) {
  const initial = Array.from(name.trim())[0]?.toUpperCase() ?? 'P';

  return (
    <span
      className='relative grid h-[18px] w-[18px] flex-shrink-0 place-items-center'
      aria-hidden='true'
    >
      <Cloud size={15} strokeWidth={1.8} />
      {compact && (
        <span
          className='absolute -bottom-[5px] -right-[6px] grid h-3.5 w-3.5 place-items-center rounded-[5px] text-[9px] font-semibold leading-none text-[var(--po-text)]'
          style={{
            background:
              'color-mix(in srgb, var(--po-sidebar) 72%, var(--po-text))',
            boxShadow: '0 0 0 1.5px var(--po-sidebar)',
          }}
        >
          {initial}
        </span>
      )}
    </span>
  );
}
