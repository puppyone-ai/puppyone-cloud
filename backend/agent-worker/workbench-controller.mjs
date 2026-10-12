/** Shared-workbench task protocol. Deliberately separate from legacy snapshot control. */
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { mkdir, chmod, chown, readFile, writeFile } from 'node:fs/promises';
import { channel } from './protocol.mjs';
import { ROOT } from './git.mjs';
import { gitRelay } from './git-relay.mjs';
import { STATE, taskBinding, initializeTask, signalTask, withWorkbenchGitLock, withTaskStopLock, workbenchGit } from './workbench-task.mjs';
const task = taskBinding(process.argv.slice(2));
const home = await initializeTask(task);
const control = channel();
const childIds = new Set();
let worker, relay, config, stopping, stopped = false, closing = false;
const configuredLease = Number(process.env.PUPPYONE_WORKBENCH_TASK_LEASE_MS || 60000);
const leaseMilliseconds = Number.isFinite(configuredLease) ? Math.min(60000, Math.max(1000, configuredLease)) : 60000;
let leaseUntil = Date.now() + leaseMilliseconds;
process.umask(0o002);
setInterval(() => { if (Date.now() >= leaseUntil) close(); }, Math.min(1000, leaseMilliseconds / 4)).unref();

async function prepareWorkspace(value) {
  if (value.restore || value.resume) throw new Error('A task cannot restore a shared workbench');
  config = undefined;
  const prepared = await withWorkbenchGitLock(async () => {
    const identity = JSON.stringify({ workbench: task.workbench, generation: task.generation });
    let stored;
    try { stored = await readFile(`${STATE}/binding.json`, 'utf8'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (stored && stored !== identity) throw new Error('Workbench generation mismatch');
    await mkdir(ROOT, { recursive: true }); await chown(ROOT, 1000, 1000); await chmod(ROOT, 0o2770);
    relay?.close();
    relay = await gitRelay(control.request, value.project_id, { authenticated: true });
    const result = stored ? await workbenchGit(task, 'inspect', value)
      : await relay.run(() => workbenchGit(task, 'prepare', value, null, relay));
    if (!stored) await writeFile(`${STATE}/binding.json`, identity, { flag: 'wx', mode: 0o600 });
    return result;
  });
  config = value;
  return prepared;
}

function start(value) {
  if (!config || worker || stopping || stopped) throw new Error('Task is not startable');
  // Drop inherited privilege and supplementary groups on both providers.
  worker = spawn('setpriv', ['--reuid', String(task.uid), '--regid', '1000', '--clear-groups',
    '--no-new-privs', '--inh-caps=-all', '--ambient-caps=-all', '--bounding-set=-all',
    'node', '/opt/puppyone-agent/worker.mjs'], {
    cwd: ROOT, stdio: ['pipe', 'pipe', 'pipe'],
    env: { PATH: '/usr/local/bin:/usr/bin:/bin', HOME: home, NODE_ENV: 'production',
      PUPPYONE_AGENT_DIR: `${home}/pi`,
      GIT_CONFIG_COUNT: '1', GIT_CONFIG_KEY_0: 'safe.directory', GIT_CONFIG_VALUE_0: ROOT },
  });
  worker.stderr.on('data', () => {});
  worker.on('error', () => control.send({ type: 'failed', error: 'Task process unavailable' }));
  createInterface({ input: worker.stdout }).on('line', line => {
    try {
      const frame = JSON.parse(line);
      if (!['checkpoint', 'tool_start', 'tool_end', 'bound_tool', 'model_request', 'model_cancel',
        'text', 'finished', 'failed', 'ready'].includes(frame.type)) throw new Error('Invalid worker message');
      if (frame.id) childIds.add(frame.id);
      control.send(frame);
    } catch (error) { control.send({ type: 'failed', error: error.message }); }
  });
  worker.on('exit', () => { if (!stopped && !stopping) control.send({ type: 'disconnected' }); });
  worker.stdin.write(JSON.stringify({ type: 'start', config: value }) + '\n');
}

async function stop() {
  if (!stopping) stopping = withTaskStopLock(task, async () => {
    await signalTask(task, 'SIGSTOP'); await signalTask(task, 'SIGKILL'); stopped = true;
  });
  try { await stopping; } finally { stopping = undefined; }
}
function close() {
  if (closing) return; closing = true;
  stop().finally(() => { relay?.close(); process.exit(0); });
}
let operations = Promise.resolve();
control.dispatch(frame => {
  if (frame.type === 'control' && frame.action === 'heartbeat') {
    if (closing) throw new Error('Task control lease expired');
    leaseUntil = Date.now() + leaseMilliseconds;
    control.send({ type: 'control_result', id: frame.id, result: {} });
  } else if (frame.type === 'control') {
    operations = operations.then(async () => {
      try {
        let result;
        if (frame.action === 'capabilities') result = { shared_workbench: 1 };
        else if (frame.action === 'prepare') result = await prepareWorkspace(frame.value);
        else if (frame.action === 'stop' || frame.action === 'park') { await stop(); result = {}; }
        else if (frame.action === 'inspect') result = await withWorkbenchGitLock(() => workbenchGit(task, 'inspect', config));
        else if (frame.action === 'finalize') {
          await stop();
          result = await withWorkbenchGitLock(() => config.readonly
            ? workbenchGit(task, 'inspect', config).then(state => ({ ...state, changed: false, automatic_commit: false }))
            : workbenchGit(task, 'finalize', config, frame.value));
        } else if (frame.action === 'push') {
          if (!stopped) throw new Error('Task must stop before publication');
          result = await withWorkbenchGitLock(() => relay.run(() =>
            workbenchGit(task, 'push', config, frame.value, relay)));
        } else throw new Error('Operation belongs to workbench lifecycle, not a task');
        control.send({ type: 'control_result', id: frame.id, result });
        if (frame.action === 'park') { relay?.close(); setImmediate(() => process.exit(0)); }
      } catch (error) { control.send({ type: 'control_result', id: frame.id, error: error.message }); }
    });
  } else if (frame.type === 'start') start(frame.config);
  else if (worker && !stopped && !stopping && (frame.type !== 'reply' || childIds.has(frame.id))) {
    childIds.delete(frame.id); worker.stdin.write(JSON.stringify(frame) + '\n');
  }
});
process.stdin.on('end', close);
