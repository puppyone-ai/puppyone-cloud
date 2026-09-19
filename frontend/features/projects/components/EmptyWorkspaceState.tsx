import React, {
  useCallback,
  useMemo,
  useRef,
  useState,
  type DragEvent,
} from 'react';
import {
  Check,
  Copy,
  FilePlus,
  Terminal,
  Upload,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ProjectInfo } from '@/lib/projectsApi';
import type { ImportJob } from '@/lib/importApi';
import { DialogBody, DialogHeader, DialogRoot, DialogSurface } from '@/components/ui/Dialog';
import { AiHandoffButton } from '@/components/ui/AiHandoffButton';
import {
  resolveDataTransferSnapshot,
  snapshotDataTransfer,
} from '@/lib/dropFiles';
import { pickDirectoryFiles } from '@/lib/directoryPicker';
import { BUTTON_HEIGHT, BUTTON_ICON_SIZE } from '@/components/ui/buttonTokens';
import {
  GitHubMark,
  GithubImportJobWorkspaceState,
  GithubOneTimeImportDialog,
} from '@/features/projects/components/imports/github';

interface EmptyWorkspaceStateProps {
  project: ProjectInfo | null;
  gitRemoteUrl?: string | null;
  onOpenGitSetup?: () => void;
  onImportFiles?: () => void;
  onFilesDrop?: (files: File[]) => void;
  onImportGitHub?: () => void;
  importJob?: ImportJob | null;
  onImportJobCreated?: (job: ImportJob) => void | Promise<void>;
  onOpenEmptyProject?: () => void;
}

type CopyTarget = 'first-agent' | 'git-agent-new' | 'git-agent-existing' | 'new' | 'existing' | null;

const COPY_RESET_MS = 1400;
const EMPTY_WORKSPACE_RAIL_WIDTH = 760;

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

function slugForDirectory(value: string): string {
  return (
    value
      .toLowerCase()
      .replaceAll(/[^a-z0-9]+/g, '-')
      .replaceAll(/^-+|-+$/g, '') || 'puppyone-project'
  );
}

function hasExternalFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer.types).includes('Files');
}

export function EmptyWorkspaceState({
  project,
  gitRemoteUrl,
  onOpenGitSetup,
  onImportFiles,
  onFilesDrop,
  onImportGitHub,
  importJob,
  onImportJobCreated,
  onOpenEmptyProject,
}: EmptyWorkspaceStateProps) {
  const [copied, setCopied] = useState<CopyTarget>(null);
  const [showGitSetupModal, setShowGitSetupModal] = useState(false);
  const [showGithubImportModal, setShowGithubImportModal] = useState(false);
  const [isDraggingFiles, setIsDraggingFiles] = useState(false);
  const projectName = project?.name || 'this project';
  const projectId = project?.id ?? null;
  const remote = gitRemoteUrl || '<git-remote-url>';
  const hasRemote = Boolean(gitRemoteUrl);
  const directoryName = slugForDirectory(project?.name || 'puppyone-project');

  const setup = useMemo(() => {
    const quotedDir = shellQuote(directoryName);
    const quotedRemote = shellQuote(remote);
    const newRepository = [
      '# Initialize a new local repository with Git',
      `mkdir ${quotedDir}`,
      `cd ${quotedDir}`,
      'git init',
      'git branch -M main',
      `git remote add origin ${quotedRemote}`,
      'git add .',
      'git commit -m "Initial context"',
      'git push -u origin main',
    ].join('\n');

    const existingRepository = [
      '# Push an existing Git repository into this project',
      `git remote add puppyone ${quotedRemote}`,
      'git push -u puppyone HEAD:main',
    ].join('\n');

    const newRepositoryPrompt = [
      `Initialize a new local repository and push it into the Puppyone project "${projectName}".`,
      '',
      `Puppyone Git remote: ${remote}`,
      '',
      'Run these commands from the folder that should become the project:',
      '```bash',
      newRepository,
      '```',
      '',
      'After pushing, report which files were imported and whether Git returned any errors.',
    ].join('\n');

    const existingRepositoryPrompt = [
      `Push the current Git repository into the Puppyone project "${projectName}".`,
      '',
      `Puppyone Git remote: ${remote}`,
      '',
      'Run these commands from the existing repository:',
      '```bash',
      existingRepository,
      '```',
      '',
      'After pushing, report which branch was pushed and whether Git returned any errors.',
    ].join('\n');

    const firstAgentPrompt = "Research today's Hacker News front page and use `puppyone fs write` to save a short inspiration digest at `Agent Output/first-run.md`.";

    return {
      newRepository,
      existingRepository,
      newRepositoryPrompt,
      existingRepositoryPrompt,
      firstAgentPrompt,
    };
  }, [directoryName, projectName, remote]);

  const copyText = useCallback(async (target: CopyTarget, text: string) => {
    if (!target) return;
    const requiresGitRemote = target !== 'first-agent';
    if (requiresGitRemote && !hasRemote) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(target);
      window.setTimeout(() => setCopied(null), COPY_RESET_MS);
    } catch {
      setCopied(null);
    }
  }, [hasRemote]);

  const handleDrop = useCallback((event: DragEvent<HTMLDivElement>) => {
    if (!hasExternalFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    setIsDraggingFiles(false);
    const snapshot = snapshotDataTransfer(event.nativeEvent);
    void resolveDataTransferSnapshot(snapshot).then((files) => {
      if (files.length > 0) onFilesDrop?.(files);
    });
  }, [onFilesDrop]);

  return (
    <div className="flex-1 bg-[var(--po-canvas)] overflow-auto min-w-0">
      <div
        className="mx-auto flex min-h-full w-full flex-col justify-center px-8 py-14"
        style={{ maxWidth: EMPTY_WORKSPACE_RAIL_WIDTH }}
      >
        {importJob ? (
          <GithubImportJobWorkspaceState
            job={importJob}
            onImportGitHub={projectId ? () => setShowGithubImportModal(true) : onImportGitHub}
            onOpenEmptyProject={onOpenEmptyProject}
          />
        ) : (
          <>
        <div className="mb-6 text-center">
          <h1 className="m-0 text-[22px] font-semibold leading-8 text-[var(--po-text)]">
            How do you want to start this workspace?
          </h1>
          <p className="mx-auto mt-2 max-w-[560px] text-[13px] leading-5 text-[var(--po-text-muted)]">
            Add existing context for your team and agents, or let an AI agent create the first file here.
          </p>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <AddContextCard
            isDraggingFiles={isDraggingFiles}
            onDragStateChange={setIsDraggingFiles}
            onDrop={handleDrop}
            onImportFiles={onImportFiles}
            onFilesSelected={onFilesDrop}
          />

          <StartWithAgentCard
            copied={copied === 'first-agent'}
            onCopyPrompt={() => copyText('first-agent', setup.firstAgentPrompt)}
          />
        </div>

        <OtherWaysRow
          onImportGitHub={projectId ? () => setShowGithubImportModal(true) : onImportGitHub}
          onStartWithGit={() => setShowGitSetupModal(true)}
          onOpenEmptyProject={onOpenEmptyProject}
        />
          </>
        )}

        {showGithubImportModal && projectId ? (
          <GithubOneTimeImportDialog
            projectId={projectId}
            onClose={() => setShowGithubImportModal(false)}
            onImportJobCreated={onImportJobCreated}
          />
        ) : null}

        {showGitSetupModal ? (
          <GitSetupDialog
            hasRemote={hasRemote}
            copied={copied}
            onClose={() => setShowGitSetupModal(false)}
            onOpenGitSetup={onOpenGitSetup}
            newCommand={setup.newRepository}
            existingCommand={setup.existingRepository}
            onCopyNewCommand={() => copyText('new', setup.newRepository)}
            onCopyExistingCommand={() => copyText('existing', setup.existingRepository)}
            onCopyNewPrompt={() => copyText('git-agent-new', setup.newRepositoryPrompt)}
            onCopyExistingPrompt={() => copyText('git-agent-existing', setup.existingRepositoryPrompt)}
          />
        ) : null}
      </div>
    </div>
  );
}

function AddContextCard({
  isDraggingFiles,
  onDragStateChange,
  onDrop,
  onImportFiles,
  onFilesSelected,
}: {
  isDraggingFiles: boolean;
  onDragStateChange: (value: boolean) => void;
  onDrop: (event: DragEvent<HTMLDivElement>) => void;
  onImportFiles?: () => void;
  onFilesSelected?: (files: File[]) => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const canPickDirectly = Boolean(onFilesSelected);

  const handleInputChange = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = event.target.files ? Array.from(event.target.files) : [];
    event.target.value = '';
    if (selectedFiles.length > 0) {
      onFilesSelected?.(selectedFiles);
    }
  }, [onFilesSelected]);

  const handleFolderPick = useCallback(async () => {
    if (!canPickDirectly) return;
    const picked = await pickDirectoryFiles();
    if (picked === null) {
      folderInputRef.current?.click();
      return;
    }
    if (picked.length > 0) {
      onFilesSelected?.(picked);
    }
  }, [canPickDirectly, onFilesSelected]);

  return (
    <section
      onDragEnter={(event) => {
        if (!hasExternalFiles(event)) return;
        event.preventDefault();
        onDragStateChange(true);
      }}
      onDragOver={(event) => {
        if (!hasExternalFiles(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
        onDragStateChange(true);
      }}
      onDragLeave={(event) => {
        const current = event.currentTarget;
        const next = event.relatedTarget;
        if (next instanceof Node && current.contains(next)) return;
        onDragStateChange(false);
      }}
      onDrop={onDrop}
      className={`flex min-h-[188px] flex-col rounded-lg border border-dashed px-5 py-4 text-left transition-[background-color,border-color] duration-150 ease-out motion-reduce:transition-none ${
        isDraggingFiles
          ? 'border-[var(--po-focus-ring)] bg-[var(--po-selected)]'
          : 'border-[var(--po-border-strong)] bg-transparent'
      }`}
    >
      <input
        ref={fileInputRef}
        type="file"
        multiple
        onChange={handleInputChange}
        style={{ display: 'none' }}
      />
      <input
        ref={folderInputRef}
        type="file"
        multiple
        {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
        onChange={handleInputChange}
        style={{ display: 'none' }}
      />
      <div className="flex h-full flex-col">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-[var(--po-border-subtle)] bg-[color-mix(in_srgb,var(--po-panel)_58%,transparent)] text-[var(--po-text-muted)]">
            <Upload
              size={16}
              strokeWidth={1.75}
              className={isDraggingFiles ? 'text-[var(--po-accent)]' : 'text-current'}
            />
          </div>
          <div className="min-w-0">
            <h2 className="m-0 text-[15px] font-semibold leading-6 text-[var(--po-text)]">
              Add existing context
            </h2>
            <p className="m-0 mt-1 text-[12px] leading-5 text-[var(--po-text-muted)]">
              Drop files or folders here, or start with an upload.
            </p>
          </div>
        </div>

        <div className="mt-auto">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                void handleFolderPick();
              }}
              disabled={!canPickDirectly}
              className="inline-flex items-center gap-2 rounded-md border border-[var(--po-text)] bg-[var(--po-text)] px-3.5 text-[12px] font-medium text-[var(--po-canvas)] transition-colors hover:bg-[var(--po-text-muted)] disabled:cursor-not-allowed disabled:opacity-45"
              style={{ height: BUTTON_HEIGHT }}
            >
              <Upload size={14} strokeWidth={1.8} />
              Upload folder
            </button>
            <button
              type="button"
              onClick={() => {
                if (canPickDirectly) {
                  fileInputRef.current?.click();
                } else {
                  onImportFiles?.();
                }
              }}
              disabled={!canPickDirectly && !onImportFiles}
              className="inline-flex items-center rounded-md border border-[var(--po-border-strong)] bg-transparent px-3 text-[12px] font-medium text-[var(--po-text)] transition-[background-color,border-color] duration-150 hover:border-[var(--po-border-strong)] hover:bg-[var(--po-border-subtle)] disabled:cursor-not-allowed disabled:opacity-45"
              style={{ height: BUTTON_HEIGHT }}
            >
              Upload files
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

function OtherWaysRow({
  onImportGitHub,
  onStartWithGit,
  onOpenEmptyProject,
}: {
  onImportGitHub?: () => void;
  onStartWithGit: () => void;
  onOpenEmptyProject?: () => void;
}) {
  return (
    <section className="mt-6 flex flex-wrap items-center gap-2">
      <InlineSourceButton
        icon={GitHubMark}
        label="Import from GitHub"
        onClick={onImportGitHub}
        disabled={!onImportGitHub}
      />
      <InlineSourceButton
        icon={Terminal}
        label="Start with Git"
        onClick={onStartWithGit}
      />
      <InlineSourceButton
        icon={FilePlus}
        label="Start blank"
        onClick={onOpenEmptyProject}
        disabled={!onOpenEmptyProject}
      />
    </section>
  );
}

function InlineSourceButton({
  icon: Icon,
  label,
  onClick,
  disabled = false,
}: {
  icon: LucideIcon | React.ComponentType<{ className?: string; size?: number; strokeWidth?: number }>;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || !onClick}
      className="inline-flex items-center gap-1.5 rounded-md border border-[var(--po-border-subtle)] bg-[color-mix(in_srgb,var(--po-panel)_42%,transparent)] px-3 text-[12px] font-medium text-[var(--po-text-muted)] transition-colors hover:border-[var(--po-border)] hover:bg-[var(--po-hover)] hover:text-[var(--po-text)] disabled:cursor-not-allowed disabled:opacity-45"
      style={{ height: BUTTON_HEIGHT }}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" size={14} strokeWidth={1.75} />
      <span>{label}</span>
    </button>
  );
}

function GitSetupDialog({
  hasRemote,
  copied,
  onClose,
  onOpenGitSetup,
  newCommand,
  existingCommand,
  onCopyNewCommand,
  onCopyExistingCommand,
  onCopyNewPrompt,
  onCopyExistingPrompt,
}: {
  hasRemote: boolean;
  copied: CopyTarget;
  onClose: () => void;
  onOpenGitSetup?: () => void;
  newCommand: string;
  existingCommand: string;
  onCopyNewCommand: () => void;
  onCopyExistingCommand: () => void;
  onCopyNewPrompt: () => void;
  onCopyExistingPrompt: () => void;
}) {
  return (
    <DialogRoot onClose={onClose} backdrop="strong">
      <DialogSurface width={720} maxHeight="min(760px, calc(100vh - 32px))" ariaLabel="Start with Git">
        <DialogHeader
          title="Start with Git"
          description="Use this workspace as a Git remote."
          onClose={onClose}
          leading={<Terminal size={17} strokeWidth={1.75} />}
        />
        <DialogBody style={{ padding: '10px 20px 20px' }}>
          {onOpenGitSetup ? (
            <button
              type="button"
              onClick={() => {
                onClose();
                onOpenGitSetup();
              }}
              className="mb-3 inline-flex items-center rounded-md border border-[var(--po-border)] bg-[color-mix(in_srgb,var(--po-panel)_58%,transparent)] px-3 text-[12px] font-medium text-[var(--po-text)] transition-colors hover:border-[var(--po-border-strong)] hover:bg-[color-mix(in_srgb,var(--po-panel)_72%,transparent)]"
              style={{ height: BUTTON_HEIGHT }}
            >
              {hasRemote ? 'Manage Git credential' : 'Create Git remote'}
            </button>
          ) : null}

          <div className="space-y-4">
            <GitCommandSection
              title="Create a new repository on the command line"
              description="Initialize a folder, commit files, and push it into this workspace."
              command={newCommand}
              disabled={!hasRemote}
              copiedCommand={copied === 'new'}
              copiedPrompt={copied === 'git-agent-new'}
              onCopyCommand={onCopyNewCommand}
              onCopyPrompt={onCopyNewPrompt}
            />
            <GitCommandSection
              title="Push an existing repository from the command line"
              description="Add Puppyone as a remote and push your current branch."
              command={existingCommand}
              disabled={!hasRemote}
              copiedCommand={copied === 'existing'}
              copiedPrompt={copied === 'git-agent-existing'}
              onCopyCommand={onCopyExistingCommand}
              onCopyPrompt={onCopyExistingPrompt}
            />
          </div>
        </DialogBody>
      </DialogSurface>
    </DialogRoot>
  );
}

function GitCommandSection({
  title,
  description,
  command,
  disabled,
  copiedCommand,
  copiedPrompt,
  onCopyCommand,
  onCopyPrompt,
}: {
  title: string;
  description: string;
  command: string;
  disabled: boolean;
  copiedCommand: boolean;
  copiedPrompt: boolean;
  onCopyCommand: () => void;
  onCopyPrompt: () => void;
}) {
  return (
    <section className="rounded-lg border border-[var(--po-border-subtle)] bg-[color-mix(in_srgb,var(--po-panel)_36%,transparent)] p-3">
      <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="m-0 text-[13px] font-semibold leading-5 text-[var(--po-text)]">
            {title}
          </h3>
          <p className="m-0 mt-0.5 text-[12px] leading-5 text-[var(--po-text-muted)]">
            {description}
          </p>
        </div>
        <AiHandoffButton
          disabled={disabled}
          onClick={onCopyPrompt}
          copied={copiedPrompt}
          copiedLabel="Prompt copied"
          style={{ flexShrink: 0 }}
        />
      </div>

      <div className="relative">
        <button
          type="button"
          disabled={disabled}
          onClick={onCopyCommand}
          aria-label={copiedCommand ? 'Copied command lines' : 'Copy command lines'}
          title={copiedCommand ? 'Copied' : 'Copy'}
          className="absolute right-2 top-2 z-10 inline-flex shrink-0 items-center justify-center rounded-md border border-[var(--po-border-subtle)] bg-[color-mix(in_srgb,var(--po-panel)_72%,transparent)] text-[var(--po-text-muted)] transition-colors hover:border-[var(--po-border)] hover:bg-[var(--po-hover)] hover:text-[var(--po-text)] disabled:cursor-not-allowed disabled:opacity-45"
          style={{ width: BUTTON_ICON_SIZE, height: BUTTON_ICON_SIZE }}
        >
          {copiedCommand ? <Check size={13} strokeWidth={1.9} /> : <Copy size={13} strokeWidth={1.75} />}
        </button>
        <pre className="m-0 max-h-[142px] overflow-auto rounded-md border border-[var(--po-border-subtle)] bg-[color-mix(in_srgb,var(--po-canvas)_58%,transparent)] p-3 pr-12 text-[11px] leading-[18px] text-[var(--po-text-muted)]">
          <code>{command}</code>
        </pre>
      </div>
    </section>
  );
}

function StartWithAgentCard({
  copied,
  onCopyPrompt,
}: {
  copied: boolean;
  onCopyPrompt: () => void;
}) {
  return (
    <section className="flex min-h-[188px] flex-col rounded-lg border border-[var(--po-border-strong)] bg-[color-mix(in_srgb,var(--po-panel)_48%,transparent)] px-5 py-4 text-left">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-[var(--po-border-subtle)] bg-[color-mix(in_srgb,var(--po-canvas)_70%,transparent)] text-[var(--po-text-muted)]">
          <Copy size={16} strokeWidth={1.75} />
        </div>
        <div className="min-w-0">
          <h2 className="m-0 text-[15px] font-semibold leading-6 text-[var(--po-text)]">
            Start with Claude Code / Cursor
          </h2>
          <p className="m-0 mt-1 text-[12px] leading-5 text-[var(--po-text-muted)]">
            Copy one sentence into Claude Code, Codex, Cursor, or another agent. It writes back with <span className="font-medium text-[var(--po-text)]">puppyone fs</span>.
          </p>
        </div>
      </div>

      <div className="mt-auto">
        <AiHandoffButton
          onClick={onCopyPrompt}
          copied={copied}
        />
      </div>
    </section>
  );
}
