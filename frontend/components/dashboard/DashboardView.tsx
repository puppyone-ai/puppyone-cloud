'use client';

import Image from 'next/image';
import React, { useMemo } from 'react';
import { CircleAlert, Cloud, FilePlus2, RotateCw } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { Dots, SkeletonBlock } from '@/components/loading';
import type { ProjectInfo } from '@/lib/projectsApi';
import styles from './DashboardView.module.css';

export interface DashboardViewProps {
  projects: ProjectInfo[];
  loading?: boolean;
  creatingProject?: boolean;
  onProjectClick: (projectId: string) => void;
  onCreateClick: () => void;
}

export function DashboardView({
  projects,
  loading,
  creatingProject = false,
  onProjectClick,
  onCreateClick,
}: DashboardViewProps) {
  const t = useTranslations('home');
  const tc = useTranslations('common');
  const format = useFormatter();
  const recentProjects = useMemo(
    () =>
      [...projects].sort(
        (left, right) => projectTimestamp(right) - projectTimestamp(left)
      ),
    [projects]
  );

  if (loading) {
    return <DashboardLoadingSkeleton label={tc('loading')} />;
  }

  return (
    <div className={styles.surface}>
      <section className={styles.homepage} aria-label={t('startPrompt')}>
        <div className={styles.launcher}>
          <header className={styles.brandLockup}>
            <span className={styles.brandMark} aria-hidden='true'>
              <Image
                className={`${styles.brandArtwork} ${styles.brandArtworkLight}`}
                src='/puppy-folder-lite.svg'
                alt=''
                width={28}
                height={28}
                unoptimized
                draggable={false}
              />
              <Image
                className={`${styles.brandArtwork} ${styles.brandArtworkDark}`}
                src='/puppy-folder-dark.svg'
                alt=''
                width={28}
                height={28}
                unoptimized
                draggable={false}
              />
            </span>
            <h1 className={styles.brandPrompt}>{t('startPrompt')}</h1>
          </header>

          {recentProjects.length > 0 && (
            <div className={styles.projectsLayout}>
              <div className={styles.recentProjects}>
                <div className={styles.projectList}>
                  {recentProjects.map(project => (
                    <button
                      key={project.id}
                      type='button'
                      className={styles.projectRow}
                      onClick={() => onProjectClick(project.id)}
                      aria-label={t('openProject', { project: project.name })}
                    >
                      <span className={styles.projectIcon} aria-hidden='true'>
                        <Cloud />
                      </span>
                      <span className={styles.projectBody}>
                        <span className={styles.projectLabel} dir='auto'>
                          {project.name}
                        </span>
                      </span>
                      <span className={styles.projectTrailing}>
                        {project.updated_at
                          ? format.relativeTime(
                              new Date(project.updated_at),
                              new Date()
                            )
                          : t('previouslyUpdated')}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          <div className={styles.primaryArea}>
            <div className={styles.entryActions}>
              <button
                type='button'
                className={styles.entryAction}
                onClick={onCreateClick}
                disabled={creatingProject}
                aria-busy={creatingProject || undefined}
              >
                <span className={styles.entryIcon} aria-hidden='true'>
                  {creatingProject ? (
                    <Dots
                      size='xs'
                      tone='neutral'
                      ariaLabel={t('creatingProject')}
                    />
                  ) : (
                    <FilePlus2 />
                  )}
                </span>
                <span className={styles.entryLabel}>
                  {creatingProject ? t('creatingProject') : t('createProject')}
                </span>
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

export function DashboardLoadingSkeleton({
  label = 'Loading...',
}: Readonly<{ label?: string }>) {
  return (
    <div className={styles.surface} aria-busy='true' aria-label={label}>
      <section className={styles.homepage}>
        <div className={styles.launcher}>
          <header className={styles.brandLockup}>
            <span className={styles.brandMark}>
              <SkeletonBlock width={28} height={28} radius={6} />
            </span>
            <SkeletonBlock
              width='min(330px, calc(100% - 40px))'
              height={18}
              radius={4}
            />
          </header>

          <div className={styles.projectsLayout}>
            <div className={styles.recentProjects}>
              <div className={styles.projectList}>
                {[0, 1, 2, 3].map(index => (
                  <div className={styles.projectSkeletonRow} key={index}>
                    <SkeletonBlock width={14} height={14} radius={3} />
                    <SkeletonBlock
                      width={`${44 + (index % 3) * 9}%`}
                      height={12}
                      radius={3}
                    />
                    <SkeletonBlock width={72} height={10} radius={3} />
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className={styles.primaryArea}>
            <div className={styles.entryActions}>
              <SkeletonBlock width={166} height={30} radius={6} />
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

export function DashboardLoadError({
  retrying,
  onRetry,
}: Readonly<{ retrying: boolean; onRetry: () => void }>) {
  const t = useTranslations('home');

  return (
    <div className={styles.surface} role='alert'>
      <section
        className={styles.errorState}
        aria-labelledby='project-load-error-title'
      >
        <CircleAlert className={styles.errorIcon} aria-hidden='true' />
        <div className={styles.errorCopy}>
          <h1 id='project-load-error-title' className={styles.errorTitle}>
            {t('loadErrorTitle')}
          </h1>
          <p className={styles.errorDescription}>{t('loadErrorDescription')}</p>
        </div>
        <button
          type='button'
          className={styles.retryButton}
          onClick={onRetry}
          disabled={retrying}
          aria-busy={retrying || undefined}
        >
          {retrying ? (
            <Dots size='xs' tone='neutral' ariaLabel={t('retrying')} />
          ) : (
            <RotateCw aria-hidden='true' />
          )}
          <span>{retrying ? t('retrying') : t('retry')}</span>
        </button>
      </section>
    </div>
  );
}

function projectTimestamp(project: ProjectInfo): number {
  if (!project.updated_at) return 0;
  const value = new Date(project.updated_at).getTime();
  return Number.isNaN(value) ? 0 : value;
}
