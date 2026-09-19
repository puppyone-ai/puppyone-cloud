import React, { useCallback, useState } from 'react';
import { Dots } from '@/components/loading';
import { createImportJob, type ImportJob } from '@/lib/importApi';
import { openOAuthPopup } from '@/lib/oauthApi';
import { DialogBody, DialogHeader, DialogRoot, DialogSurface } from '@/components/ui/Dialog';
import { BUTTON_HEIGHT } from '@/components/ui/buttonTokens';
import { GitHubMark } from '@/features/projects/components/imports/github/GitHubMark';
import {
  getGithubImportErrorMessage,
  normalizeGithubRepositoryUrl,
} from '@/features/projects/components/imports/github/githubImportUtils';

interface GithubOneTimeImportDialogProps {
  projectId: string;
  onClose: () => void;
  onImportJobCreated?: (job: ImportJob) => void | Promise<void>;
}

export function GithubOneTimeImportDialog({
  projectId,
  onClose,
  onImportJobCreated,
}: GithubOneTimeImportDialogProps) {
  const [repoUrl, setRepoUrl] = useState('');
  const [isImporting, setIsImporting] = useState(false);
  const [isAuthorizing, setIsAuthorizing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authorizationNotice, setAuthorizationNotice] = useState<string | null>(null);
  const normalizedUrl = normalizeGithubRepositoryUrl(repoUrl);
  const busy = isImporting || isAuthorizing;

  const handleImport = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!normalizedUrl) {
      setError('Paste a valid GitHub repository URL.');
      return;
    }

    setIsImporting(true);
    setError(null);
    try {
      const job = await createImportJob({
        project_id: projectId,
        source_url: normalizedUrl,
        provider: 'github',
      });
      await onImportJobCreated?.(job);
      setIsImporting(false);
      onClose();
    } catch (err) {
      setError(getGithubImportErrorMessage(err));
      setIsImporting(false);
    }
  }, [normalizedUrl, onClose, onImportJobCreated, projectId]);

  const handleAuthorizeGithub = useCallback(async () => {
    setIsAuthorizing(true);
    setError(null);
    setAuthorizationNotice(null);
    try {
      const completed = await openOAuthPopup('github');
      setIsAuthorizing(false);
      setAuthorizationNotice(
        completed
          ? 'GitHub window closed. Try importing the repository now.'
          : 'GitHub authorization did not finish. Try again.',
      );
    } catch (err) {
      setError(getGithubImportErrorMessage(err));
      setIsAuthorizing(false);
    }
  }, []);

  return (
    <DialogRoot onClose={busy ? undefined : onClose} backdrop="strong" dismissOnBackdrop={!busy}>
      <DialogSurface width={560} maxHeight="min(560px, calc(100vh - 32px))" ariaLabel="Import from GitHub">
        <DialogHeader
          title="Import from GitHub"
          description="Paste a repository URL. Puppyone copies it into this workspace once."
          onClose={busy ? undefined : onClose}
          leading={<GitHubMark className="h-4 w-4 text-[var(--po-text-muted)]" />}
        />
        <DialogBody style={{ padding: '10px 20px 20px' }}>
          <form onSubmit={handleImport} className="space-y-4">
            <label className="block">
              <span className="mb-1 block text-[12px] font-medium leading-5 text-[var(--po-text)]">
                Repository URL
              </span>
              <input
                type="text"
                inputMode="url"
                value={repoUrl}
                onChange={(event) => {
                  setRepoUrl(event.target.value);
                  setError(null);
                  setAuthorizationNotice(null);
                }}
                placeholder="https://github.com/org/repo"
                autoFocus
                className="h-9 w-full rounded-md border border-[var(--po-border)] bg-[var(--po-panel)] px-3 text-[13px] text-[var(--po-text)] outline-none transition-colors placeholder:text-[var(--po-text-subtle)] focus:border-[var(--po-focus-ring)]"
              />
            </label>

            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="m-0 text-[12px] leading-5 text-[var(--po-text-muted)]">
                Public repositories import by URL. Private repositories require GitHub authorization.
              </p>
              <button
                type="button"
                onClick={handleAuthorizeGithub}
                disabled={busy}
                className="inline-flex items-center rounded-md border border-[var(--po-border-subtle)] bg-transparent px-2.5 text-[12px] font-medium text-[var(--po-text-muted)] transition-colors hover:border-[var(--po-border)] hover:bg-[var(--po-hover)] hover:text-[var(--po-text)] disabled:cursor-not-allowed disabled:opacity-45"
                style={{ height: BUTTON_HEIGHT }}
              >
                {isAuthorizing ? 'Authorizing...' : 'Authorize GitHub'}
              </button>
            </div>

            {isAuthorizing ? (
              <div className="flex items-start gap-2 rounded-md border border-[var(--po-border-subtle)] bg-[color-mix(in_srgb,var(--po-panel)_42%,transparent)] px-3 py-2 text-[12px] leading-5 text-[var(--po-text-muted)]">
                <Dots size="xs" ariaLabel="Authorizing" />
                <div>
                  <div className="font-medium text-[var(--po-text)]">Authorizing GitHub</div>
                  <div>Finish the GitHub window. This dialog stays open until authorization returns.</div>
                </div>
              </div>
            ) : null}

            {authorizationNotice ? (
              <p className="m-0 rounded-md border border-[var(--po-border-subtle)] bg-[color-mix(in_srgb,var(--po-panel)_42%,transparent)] px-3 py-2 text-[12px] leading-5 text-[var(--po-text-muted)]">
                {authorizationNotice}
              </p>
            ) : null}

            {error ? (
              <p role="alert" className="m-0 rounded-md border border-[color-mix(in_srgb,var(--po-danger)_42%,transparent)] bg-[color-mix(in_srgb,var(--po-danger)_8%,transparent)] px-3 py-2 text-[12px] leading-5 text-[var(--po-danger)]">
                {error}
              </p>
            ) : null}

            {isImporting ? (
              <div className="flex items-start gap-2 rounded-md border border-[var(--po-border-subtle)] bg-[color-mix(in_srgb,var(--po-panel)_42%,transparent)] px-3 py-2 text-[12px] leading-5 text-[var(--po-text-muted)]">
                <Dots size="xs" ariaLabel="Creating import job" />
                <div>
                  <div className="font-medium text-[var(--po-text)]">Creating import job</div>
                  <div>Puppyone will keep importing in the background after this dialog closes.</div>
                </div>
              </div>
            ) : null}

            <div className="flex items-center justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                disabled={busy}
                className="inline-flex items-center rounded-md border border-[var(--po-border-subtle)] bg-transparent px-3 text-[12px] font-medium text-[var(--po-text-muted)] transition-colors hover:border-[var(--po-border)] hover:bg-[var(--po-hover)] hover:text-[var(--po-text)] disabled:cursor-not-allowed disabled:opacity-45"
                style={{ height: BUTTON_HEIGHT }}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!normalizedUrl || busy}
                className="inline-flex items-center gap-2 rounded-md border border-[var(--po-text)] bg-[var(--po-text)] px-3.5 text-[12px] font-medium text-[var(--po-canvas)] transition-colors hover:bg-[var(--po-text-muted)] disabled:cursor-not-allowed disabled:opacity-45"
                style={{ height: BUTTON_HEIGHT }}
              >
                {isImporting ? (
                  <>
                    <Dots size="xs" ariaLabel="Importing" />
                    Importing
                  </>
                ) : 'Import repository'}
              </button>
            </div>
          </form>
        </DialogBody>
      </DialogSurface>
    </DialogRoot>
  );
}
