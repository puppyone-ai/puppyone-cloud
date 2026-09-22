'use client';

import {
  AccessChainIcon,
  AgentEntryIcon,
  DesktopChromeAction,
} from '@/components/chrome/DesktopChrome';
import { useProjectSession } from '@/features/workspace/session';
import { useWorkspaceActions } from '@/features/workspace/responsive';

/** Project utilities live on the right edge; they never replace the main view. */
export function ProjectAuxiliaryActions() {
  const panel = useProjectSession(state => state.panel);
  const openPanel = useProjectSession(state => state.openPanel);
  const closePanel = useProjectSession(state => state.closePanel);
  const { setFilesOpen } = useWorkspaceActions();
  const accessOpen = panel.type === 'access_list';
  const chatOpen = panel.type === 'workspace_chat';

  return (
    <div className='workspace-project-utilities' style={{ display: 'flex', alignItems: 'center', gap: 3, height: 24 }}>
      <DesktopChromeAction
        active={accessOpen}
        data-workspace-access-trigger=''
        expanded={accessOpen}
        label='Access'
        icon={<AccessChainIcon />}
        onClick={() => {
          setFilesOpen(false);
          if (accessOpen) closePanel();
          else openPanel({ type: 'access_list', view: 'overview' });
        }}
      />
      <DesktopChromeAction
        active={chatOpen}
        data-workspace-chat-trigger=''
        expanded={chatOpen}
        label='Chat'
        icon={<AgentEntryIcon />}
        onClick={() => {
          setFilesOpen(false);
          if (chatOpen) closePanel();
          else openPanel({ type: 'workspace_chat' });
        }}
      />
    </div>
  );
}
