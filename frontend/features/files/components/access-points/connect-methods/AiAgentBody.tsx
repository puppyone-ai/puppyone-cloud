'use client';

import { ActivationCard } from '@/features/files/components/access-points/connect-methods/ActivationCard';

export function AiAgentBody({
  scopeName,
  activated,
  canActivate,
  activating,
  activationError,
  onActivate,
  onOpenChat,
}: {
  readonly scopeName: string;
  readonly activated: boolean;
  readonly canActivate: boolean;
  readonly activating: boolean;
  readonly activationError: string | null;
  readonly onActivate: () => void;
  readonly onOpenChat: () => void;
}) {
  if (!activated) {
    return (
      <ActivationCard
        title="Activate the AI Agent for this scope"
        body="Creates the in-app chat agent bound to this scope. Permissions come from the scope mode above, so there is no separate folder picker here."
        actionLabel={activating ? 'Activating...' : 'Activate'}
        disabled={!canActivate || activating}
        error={activationError}
        onAction={onActivate}
      />
    );
  }

  return (
    <ActivationCard
      title="AI Agent is ready"
      body={`Open an in-app chat with access to ${scopeName}. The chat runtime uses this scope directly; MCP setup belongs in the MCP connection method.`}
      actionLabel="Open chat"
      onAction={onOpenChat}
    />
  );
}
