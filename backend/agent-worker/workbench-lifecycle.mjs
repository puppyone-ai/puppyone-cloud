/** Workspace creation channel: Git initialization and Tier 2 host-export control only. */
import { channel } from './protocol.mjs';
import { runtimeManifest } from './runtime-contract.mjs';
import { requireWorkspace } from './workbench-state.mjs';
import { initializeWorkbench } from './workbench-initialize.mjs';
import { gitRelay } from './git-relay.mjs';
import { withWorkbenchGitLock, sharedGitCommand } from './workbench-task.mjs';
import {
  ensureClientKey,
  grantHostExport,
  mountHostExport,
  revokeHostExport,
  startHostExport,
  unmountHostExport,
} from './workbench-host-export.mjs';

const [workbench, generation] = process.argv.slice(2);
if (process.argv.length !== 4 || process.getuid() !== 0) throw new Error('Invalid workspace lifecycle controller');
process.umask(0o002);
const control = channel();
let pending = Promise.resolve();
const WORKSPACE_GIT = new Set(['save', 'snapshot', 'push-snapshot', 'sync', 'sync-finish', 'sync-abort', 'publish']);

// Platform-owned Workspace Git. The relay exists only for this one command, so
// nothing a task or terminal starts can reach the Git service through it.
async function workspaceGit({ action, config, value }) {
  if (!WORKSPACE_GIT.has(action)) throw new Error('Unsupported workspace Git operation');
  await requireWorkspace({ workbench, generation });
  return withWorkbenchGitLock(async () => {
    const relay = await gitRelay(control.request, config.project_id, { authenticated: true });
    try { return await relay.run(() => sharedGitCommand(1000, action, config, value, relay)); }
    finally { relay.close(); }
  });
}
control.dispatch(frame => {
  if (frame.type !== 'control') throw new Error('Workspace lifecycle accepts control only');
  pending = pending.then(async () => {
    try {
      let result;
      if (frame.action === 'capabilities') {
        result = { workbench_lifecycle: 1, workspace_git: 2, host_export: 1, runtime: runtimeManifest };
      } else if (frame.action === 'ready') {
        await requireWorkspace({ workbench, generation });
        result = {};
      } else if (frame.action === 'initialize') {
        result = await initializeWorkbench({ workbench, generation }, frame.value, control.request);
      } else if (frame.action === 'workspace-git') {
        result = await workspaceGit(frame.value);
      } else if (frame.action === 'host-export-start') {
        await requireWorkspace({ workbench, generation });
        result = await startHostExport(frame.value || {});
      } else if (frame.action === 'host-export-grant') {
        await requireWorkspace({ workbench, generation });
        result = await grantHostExport(frame.value || {});
      } else if (frame.action === 'host-export-revoke') {
        await requireWorkspace({ workbench, generation });
        result = await revokeHostExport(frame.value || {});
      } else if (frame.action === 'host-export-client-key') {
        result = await ensureClientKey();
      } else if (frame.action === 'host-export-mount') {
        result = await mountHostExport({ ...(frame.value || {}), workbench, generation });
      } else if (frame.action === 'host-export-unmount') {
        result = await unmountHostExport();
      } else {
        throw new Error('Unsupported workspace lifecycle operation');
      }
      control.send({ type: 'control_result', id: frame.id, result });
    } catch (error) {
      control.send({ type: 'control_result', id: frame.id, error: error.message });
    }
  });
});
process.stdin.on('end', () => { control.close(); pending.finally(() => process.exit(0)); });
