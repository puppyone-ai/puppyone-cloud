import { Check } from 'lucide-react';
import { Dots } from '@/components/loading';
import type { ImportJob } from '@/lib/importApi';
import { BUTTON_HEIGHT } from '@/components/ui/buttonTokens';
import {
  getGithubImportPhaseLabel,
  getGithubImportSourceLabel,
} from '@/features/projects/components/imports/github/githubImportUtils';

interface GithubImportJobWorkspaceStateProps {
  job: ImportJob;
  onImportGitHub?: () => void;
  onOpenEmptyProject?: () => void;
}

export function GithubImportJobWorkspaceState({
  job,
  onImportGitHub,
  onOpenEmptyProject,
}: GithubImportJobWorkspaceStateProps) {
  const isActive = job.status === 'queued' || job.status === 'running';
  const isFailed = job.status === 'failed';
  const progress = Math.max(0, Math.min(100, job.progress || (isActive ? 8 : 100)));
  const sourceLabel = getGithubImportSourceLabel(job.source_url);

  return (
    <section className="mx-auto flex w-full max-w-[560px] flex-col items-center text-center">
      <div className="mb-5 inline-flex h-11 w-11 items-center justify-center rounded-lg border border-[var(--po-border-subtle)] bg-[color-mix(in_srgb,var(--po-panel)_52%,transparent)] text-[var(--po-text)]">
        {isActive ? (
          <Dots size="sm" ariaLabel="Importing" />
        ) : isFailed ? (
          <span className="text-[18px] leading-none">!</span>
        ) : (
          <Check className="h-5 w-5" strokeWidth={1.9} />
        )}
      </div>
      <h1 className="m-0 text-[22px] font-semibold leading-8 text-[var(--po-text)]">
        {isActive ? 'Importing from GitHub' : isFailed ? 'GitHub import failed' : 'GitHub import finished'}
      </h1>
      <p className="mt-2 max-w-[460px] text-[13px] leading-5 text-[var(--po-text-muted)]">
        {isActive
          ? 'This import is running in the background. You can leave this page and come back later.'
          : isFailed
            ? job.error_message || 'The repository could not be imported.'
            : 'The workspace will refresh with the imported files.'}
      </p>

      <div className="mt-6 w-full rounded-lg border border-[var(--po-border-subtle)] bg-[color-mix(in_srgb,var(--po-panel)_36%,transparent)] p-4 text-left">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-[13px] font-medium leading-5 text-[var(--po-text)]" title={job.source_url}>
              {job.name || sourceLabel}
            </div>
            <div className="mt-0.5 text-[12px] leading-5 text-[var(--po-text-muted)]">
              {job.message || getGithubImportPhaseLabel(job.phase)}
            </div>
          </div>
          <div className="shrink-0 text-[12px] font-medium tabular-nums text-[var(--po-text-muted)]">
            {progress}%
          </div>
        </div>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[var(--po-border-subtle)]">
          <div
            className="h-full rounded-full bg-[var(--po-accent)] transition-[width] duration-200"
            style={{ width: `${Math.max(4, progress)}%` }}
          />
        </div>
      </div>

      {isFailed ? (
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <button
            type="button"
            onClick={onImportGitHub}
            disabled={!onImportGitHub}
            className="inline-flex items-center rounded-md border border-[var(--po-text)] bg-[var(--po-text)] px-3.5 text-[12px] font-medium text-[var(--po-canvas)] transition-colors hover:bg-[var(--po-text-muted)] disabled:cursor-not-allowed disabled:opacity-45"
            style={{ height: BUTTON_HEIGHT }}
          >
            Try another repository
          </button>
          <button
            type="button"
            onClick={onOpenEmptyProject}
            disabled={!onOpenEmptyProject}
            className="inline-flex items-center rounded-md border border-[var(--po-border-subtle)] bg-transparent px-3 text-[12px] font-medium text-[var(--po-text-muted)] transition-colors hover:border-[var(--po-border)] hover:bg-[var(--po-hover)] hover:text-[var(--po-text)] disabled:cursor-not-allowed disabled:opacity-45"
            style={{ height: BUTTON_HEIGHT }}
          >
            Start blank
          </button>
        </div>
      ) : null}
    </section>
  );
}
