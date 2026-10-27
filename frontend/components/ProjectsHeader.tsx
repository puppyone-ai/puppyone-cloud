'use client';

import { useEffect, type ReactNode } from 'react';
import { WorkspaceLink as Link, usePendingNavigation, returnToFiles } from '@/features/workspace/navigation';
import { useRouter } from 'next/navigation';
import { Files, GitBranch, LoaderCircle } from 'lucide-react';
import { ProjectHeaderFrame } from './project/ProjectHeaderFrame';
import { ProjectHeaderBreadcrumbs, type BreadcrumbSegment } from './project/ProjectHeaderBreadcrumbs';
export { ProjectHeaderBreadcrumbs, type BreadcrumbSegment } from './project/ProjectHeaderBreadcrumbs';
import { useSessionValue } from '@/features/workspace/session';
import styles from './ProjectsHeader.module.css';
import { WorkspaceNavigationButton } from '@/components/sidebar/WorkspaceNavigationButton';
import { WorkspaceFilesButton } from '@/components/sidebar/WorkspaceFilesButton';
import { useWorkspaceRegions } from '@/features/workspace/regions';

export type EditorType = 'table' | 'monaco';
export type ViewType = 'grid' | 'list' | 'explorer';
type ProjectView = 'files' | 'git';

const PROJECT_VIEWS = [
  ['files', 'Files', 'data', Files],
  ['git', 'Git', 'changes', GitBranch],
] as const;

type ProjectsHeaderProps = {
  pathSegments?: BreadcrumbSegment[];
  pathContent?: ReactNode;
  projectId: string | null;
  onProjectsRefresh?: () => void;
  onBack?: () => void;
  accessPointCount?: number;
  actionSlot?: ReactNode;
  activeView?: ProjectView;
};

export function ProjectsHeader({
  pathSegments = [],
  pathContent,
  projectId,
  onBack,
  actionSlot,
  activeView = 'files',
}: ProjectsHeaderProps) {
  const router = useRouter();
  const regions = useWorkspaceRegions();
  const [filesHref] = useSessionValue('filesHref');
  const pendingHref = usePendingNavigation();
  const hasPathContent = pathContent !== undefined || pathSegments.length > 0;

  // Warm the primary project route chunks after the current view has painted.
  // Next route prefetch runs in production only; it is not a development
  // latency fix. Pending feedback is independent of the committed selection.
  useEffect(() => {
    if (!projectId) return;
    const timer = window.setTimeout(() => {
      for (const [, , route] of PROJECT_VIEWS) {
        router.prefetch(`/projects/${projectId}/${route}`);
      }
    }, 180);
    return () => window.clearTimeout(timer);
  }, [projectId, router]);

  return (
    <ProjectHeaderFrame headerRef={regions.header} actions={actionSlot}>
      {/* LEFT SIDE: Back + Breadcrumbs */}
      <>
        <WorkspaceNavigationButton />
        {onBack && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              paddingLeft: 8,
              paddingRight: 8,
            }}
          >
            <button
              onClick={onBack}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 30,
                height: 30,
                background: 'transparent',
                border: 'none',
                borderRadius: 6,
                cursor: 'pointer',
                color: 'var(--po-text-subtle)',
                transition: 'all 0.15s',
              }}
              onMouseEnter={e => {
                e.currentTarget.style.background = 'var(--po-hover)';
                e.currentTarget.style.color = 'var(--po-text)';
              }}
              onMouseLeave={e => {
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.color = 'var(--po-text-subtle)';
              }}
              title='Back to Home'
            >
              <svg
                width='16'
                height='16'
                viewBox='0 0 24 24'
                fill='none'
                stroke='currentColor'
                strokeWidth='2'
                strokeLinecap='round'
                strokeLinejoin='round'
              >
                <path d='M19 12H5' />
                <path d='M12 19l-7-7 7-7' />
              </svg>
            </button>
          </div>
        )}

        {/* Current project / file identity comes before the view switch. */}
        <div className={styles.pathSlot}>
        {pathContent !== undefined
          ? pathContent
          : pathSegments.length > 0 && (
              <ProjectHeaderBreadcrumbs pathSegments={pathSegments} />
            )}

        </div>

        {projectId && hasPathContent && (
          <span aria-hidden='true' style={{ width: 1, height: 18, background: 'var(--po-divider)', flexShrink: 0 }} />
        )}
        {projectId && (
          <nav aria-label='Project views' style={{ display: 'flex', alignItems: 'center', gap: 3, flexShrink: 0 }}>
            {activeView === 'files' && <WorkspaceFilesButton />}
            {PROJECT_VIEWS.map(([view, label, route, Icon]) => {
              const href = view === 'files' ? returnToFiles(projectId, filesHref) : `/projects/${projectId}/${route}`;
              const selected = activeView === view;
              const pending = pendingHref === href && activeView !== view;
              return (
                <Link
                  key={view}
                  data-project-view={view}
                  href={href}
                  aria-label={label}
                  title={label}
                  aria-current={activeView === view ? 'page' : undefined}
                  aria-busy={pending || undefined}
                  onPointerEnter={() => router.prefetch(href)}
                  onFocus={() => router.prefetch(href)}
                  className={selected
                    ? 'inline-flex h-6 items-center gap-1.5 rounded-[5px] bg-[var(--po-selected)] px-2 text-[12px] font-medium text-[var(--po-text)] no-underline'
                    : 'inline-flex h-6 w-8 items-center justify-center rounded-[5px] text-[var(--po-text-muted)] no-underline transition-colors hover:bg-[var(--po-hover)] hover:text-[var(--po-text)]'}
                >
                  {pending ? <LoaderCircle size={15} strokeWidth={1.8} className='animate-spin motion-reduce:animate-none' aria-hidden='true' /> : <Icon size={15} strokeWidth={1.8} aria-hidden='true' />}
                  {selected && <span className={styles.viewLabel}>{label}</span>}
                </Link>
              );
            })}
          </nav>
        )}
      </>
    </ProjectHeaderFrame>
  );
}
