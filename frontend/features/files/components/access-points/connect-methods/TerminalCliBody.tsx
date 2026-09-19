'use client';

import { CliCredentialIssuePanel } from '@/features/files/components/access-points/connect-methods/CliCredentialIssuePanel';
import { CommandBlock } from '@/features/files/components/access-points/connect-methods/CommandBlock';
import { Disclosure } from '@/features/files/components/access-points/connect-methods/Disclosure';
import { NumberedStep } from '@/features/files/components/access-points/connect-methods/NumberedStep';
import { PromptBlock } from '@/features/files/components/access-points/connect-methods/PromptBlock';
import { buildTerminalCliPrompt } from '@/lib/accessPointCliPrompt';
import type { RepositoryTarget } from '@puppyone/cloud-core';

export function TerminalCliBody({
  apiBase,
  connectorId,
  target,
  profileName,
  scopeName,
}: {
  readonly apiBase: string;
  readonly connectorId: string;
  readonly target: RepositoryTarget;
  readonly profileName: string;
  readonly scopeName: string;
}) {
  return (
    <CliCredentialIssuePanel
      connectorId={connectorId}
      target={target}
    >
      {(accessKey) => {
        const { installLine, loginLine, exploreLines, fileLines, prompt } = buildTerminalCliPrompt({
          apiBase,
          accessKey,
          profileName,
          scopeName,
        });
        return (
          <>
            <PromptBlock prompt={prompt} />
            <Disclosure summary="Show install steps">
              <NumberedStep number={1} title="Install once">
                <CommandBlock lines={[installLine]} />
              </NumberedStep>
              <NumberedStep number={2} title="Sign in to this scope">
                <CommandBlock lines={[loginLine]} />
              </NumberedStep>
              <NumberedStep number={3} title="Explore safely">
                <CommandBlock lines={exploreLines} />
              </NumberedStep>
              <NumberedStep number={4} title="Read & write files">
                <CommandBlock lines={fileLines} />
              </NumberedStep>
            </Disclosure>
          </>
        );
      }}
    </CliCredentialIssuePanel>
  );
}
