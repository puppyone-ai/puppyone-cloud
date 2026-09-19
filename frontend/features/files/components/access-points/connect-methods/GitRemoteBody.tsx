'use client';

import { CommandBlock, LabeledCommandBlock } from '@/features/files/components/access-points/connect-methods/CommandBlock';
import { Disclosure } from '@/features/files/components/access-points/connect-methods/Disclosure';
import { GitCredentialIssuePanel } from '@/features/files/components/access-points/connect-methods/GitCredentialIssuePanel';
import { NumberedStep } from '@/features/files/components/access-points/connect-methods/NumberedStep';
import { PromptBlock } from '@/features/files/components/access-points/connect-methods/PromptBlock';
import { buildGitSyncPrompt } from '@/lib/accessPointCliPrompt';
import type { RepositoryTarget } from '@puppyone/cloud-core';

export function GitRemoteBody({
  connectorId,
  gitUrl,
  scopeMode,
  scopeName,
  target,
}: {
  readonly connectorId: string;
  readonly gitUrl: string;
  readonly scopeMode: 'r' | 'rw';
  readonly scopeName: string;
  readonly target: RepositoryTarget;
}) {
  const {
    cloneLines,
    existingFolderLines,
    workflowLines,
    prompt,
  } = buildGitSyncPrompt({ gitUrl, scopeName, directoryName: scopeName });

  return (
    <>
      <GitCredentialIssuePanel
        connectorId={connectorId}
        gitUrl={gitUrl}
        scopeMode={scopeMode}
        target={target}
      />
      <PromptBlock prompt={prompt} />
      <Disclosure summary="Show Git commands">
        <NumberedStep number={1} title="Clone to a new folder">
          <CommandBlock lines={cloneLines} />
        </NumberedStep>
        <NumberedStep
          number={2}
          title="Publish an existing folder"
          hint="Use this when the local folder already exists and should become this scope's Git worktree."
        >
          <LabeledCommandBlock label="Existing folder" lines={existingFolderLines} />
        </NumberedStep>
        <NumberedStep number={3} title="Day-to-day workflow">
          <CommandBlock lines={workflowLines} />
        </NumberedStep>
      </Disclosure>
    </>
  );
}
