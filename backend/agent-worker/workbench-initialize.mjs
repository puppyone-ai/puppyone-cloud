/** One platform-owned cold initialization; never callable through a Chat controller. */
import { mkdir, open, readFile, writeFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { ROOT, exists, git } from './git.mjs';
import { initializeGitControl } from './workbench-git-layout.mjs';
import { gitRelay } from './git-relay.mjs';
import { STATE, UUID, initializeControlState, requireWorkspace } from './workbench-state.mjs';
import { withWorkbenchGitLock, sharedGitCommand } from './workbench-task.mjs';

export async function initializeWorkbench(binding, config, exchange) {
  if (![binding.workbench, binding.generation].every(value => typeof value === 'string' && UUID.test(value)))
    throw new Error('Invalid workspace lifecycle binding');
  if (!config || !['sha1', 'sha256'].includes(config.object_format)
    || typeof config.project_id !== 'string' || !config.project_id
    || typeof config.target_ref !== 'string' || !config.target_ref.startsWith('refs/heads/')
    || (config.base_oid != null && !(config.object_format === 'sha1' ? /^[0-9a-f]{40}$/ : /^[0-9a-f]{64}$/).test(config.base_oid)))
    throw new Error('Invalid workspace initialization configuration');
  await git('/tmp', ['check-ref-format', config.target_ref]);
  if (config.source_ref) await git('/tmp', ['check-ref-format', config.source_ref]);
  await initializeControlState();
  return withWorkbenchGitLock(async () => {
    // Completed and ambiguous attempts both retain their exact original initialization input.
    const intent = JSON.stringify({ ...binding, project: config.project_id, format: config.object_format,
      target: config.target_ref, source: config.source_ref || config.target_ref, base: config.base_oid ?? null });
    const file = `${STATE}/initialization.json`;
    if (await exists(`${STATE}/binding.json`)) {
      await requireWorkspace(binding);
      if (await readFile(file, 'utf8') !== intent) throw new Error('Workspace initialization intent mismatch');
      return sharedGitCommand(1000, 'inspect', config);
    }
    let retry = false;
    try { await writeFile(file, intent, { flag: 'wx', mode: 0o600 }); }
    catch (error) {
      if (error.code !== 'EEXIST' || await readFile(file, 'utf8') !== intent)
        throw new Error('Workspace initialization intent mismatch');
      retry = true;
    }
    await mkdir(ROOT, { recursive: true });
    // Never chown/chmod a symlink swapped into the user-writable workspace parent.
    const root = await open(ROOT, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_DIRECTORY);
    try { await root.chown(1000, 1000); await root.chmod(0o2770); }
    finally { await root.close(); }
    await initializeGitControl(config.object_format);
    let result;
    if (retry && await exists(`${ROOT}/.git`)) {
      // Confirm a completed checkout after a lost ACK. Never reset/clean an uncertain partial copy.
      result = await sharedGitCommand(1000, 'verify-initialization', config);
    } else {
      const relay = await gitRelay(exchange, config.project_id, { authenticated: true });
      try { result = await relay.run(() => sharedGitCommand(1000, 'prepare', config, null, relay)); }
      finally { relay.close(); }
    }
    await writeFile(`${STATE}/binding.json`, JSON.stringify(binding), { flag: 'wx', mode: 0o600 });
    return result;
  });
}
