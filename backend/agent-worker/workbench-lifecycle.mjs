/** Workspace creation channel: Git initialization only, no task/model execution. */
import { channel } from './protocol.mjs';
import { runtimeManifest } from './runtime-contract.mjs';
import { requireWorkspace } from './workbench-state.mjs';
import { initializeWorkbench } from './workbench-initialize.mjs';
import { gitRelay } from './git-relay.mjs';
import { withWorkbenchGitLock, sharedGitCommand } from './workbench-task.mjs';
const [workbench, generation] = process.argv.slice(2);
if (process.argv.length !== 4 || process.getuid() !== 0) throw new Error('Invalid workspace lifecycle controller');
process.umask(0o002);
const control = channel();
let pending = Promise.resolve();
const WORKSPACE_GIT = new Set(['save', 'sync', 'sync-finish', 'sync-abort', 'publish']);

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
      if (frame.action === 'capabilities') result = { workbench_lifecycle: 1, workspace_git: 1, runtime: runtimeManifest };
      else if (frame.action === 'ready') { await requireWorkspace({workbench, generation}); result = {}; }
      else if (frame.action === 'initialize')
        result = await initializeWorkbench({ workbench, generation }, frame.value, control.request);
      else if (frame.action === 'workspace-git') result = await workspaceGit(frame.value);
      else throw new Error('Unsupported workspace lifecycle operation');
      control.send({ type: 'control_result', id: frame.id, result });
    } catch (error) { control.send({ type: 'control_result', id: frame.id, error: error.message }); }
  });
});
process.stdin.on('end', () => { control.close(); pending.finally(() => process.exit(0)); });
