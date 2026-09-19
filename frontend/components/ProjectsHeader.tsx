'use client';

import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Files, GitBranch } from 'lucide-react';
import { APP_Z_INDEX } from '@/lib/zIndex';
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
  const [pendingView, setPendingView] = useState<ProjectView | null>(null);
  const displayedView = pendingView ?? activeView;
  const hasPathContent = pathContent !== undefined || pathSegments.length > 0;

  useEffect(() => {
    setPendingView(null);
  }, [activeView, projectId]);

  // Warm the primary project route chunks after the current view has painted.
  // Next route prefetch runs in production only; it is not a development
  // latency fix. The pending selection below acknowledges a real navigation.
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
    <header ref={regions.header} data-workspace-header style={headerStyle} className={styles.header}>
      {/* LEFT SIDE: Back + Breadcrumbs */}
      <div style={headerLeftStyle} className={styles.left}>
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
              const href = view === 'files' && filesHref ? filesHref : `/projects/${projectId}/${route}`;
              const selected = displayedView === view;
              const pending = pendingView === view && activeView !== view;
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
                  onClick={event => {
                    if (event.defaultPrevented) return;
                    if (
                      !event.metaKey &&
                      !event.ctrlKey &&
                      !event.shiftKey &&
                      !event.altKey &&
                      view !== activeView
                    ) {
                      setPendingView(view);
                    }
                  }}
                  className={selected
                    ? 'inline-flex h-6 items-center gap-1.5 rounded-[5px] bg-[var(--po-selected)] px-2 text-[12px] font-medium text-[var(--po-text)] no-underline'
                    : 'inline-flex h-6 w-8 items-center justify-center rounded-[5px] text-[var(--po-text-muted)] no-underline transition-colors hover:bg-[var(--po-hover)] hover:text-[var(--po-text)]'}
                >
                  <Icon size={15} strokeWidth={1.8} aria-hidden='true' />
                  {selected && <span className={styles.viewLabel}>{label}</span>}
                </Link>
              );
            })}
          </nav>
        )}
      </div>
      {actionSlot && <div style={headerActionStyle} className={styles.actions}>{actionSlot}</div>}
    </header>
  );
}

// Styles
const headerStyle: CSSProperties = {
  height: 'var(--project-header-height, 46px)',
  width: '100%',
  minWidth: 0,
  minHeight: 'var(--project-header-height, 46px)',
  maxHeight: 'var(--project-header-height, 46px)',
  boxSizing: 'border-box',
  flex: '0 0 auto',
  paddingLeft: 'var(--project-header-padding, 16px)',
  paddingRight: 'var(--project-header-padding, 16px)',
  display: 'flex',
  flexWrap: 'nowrap',
  alignItems: 'center',
  justifyContent: 'space-between',
  // Persistent workspace chrome stays distinct from the document surface in
  // both themes. Reuse the Desktop-aligned header token, not a local gray.
  background: 'var(--po-header)',
  borderBottom: '1px solid var(--po-divider)',
  position: 'relative',
  zIndex: APP_Z_INDEX.chrome,
  overflow: 'visible',
};

const headerLeftStyle: CSSProperties = {
  flex: '1 1 auto',
  display: 'flex',
  alignItems: 'center',
  minWidth: 0,
  overflow: 'visible',
};

const headerActionStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'flex-end',
  flexShrink: 0,
  marginLeft: 'auto',
  position: 'relative',
  zIndex: APP_Z_INDEX.chromeRaised,
};
