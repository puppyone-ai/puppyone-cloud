/** Shared-workbench task protocol. Deliberately separate from legacy snapshot control. */
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { writeFile } from 'node:fs/promises';
import { channel } from './protocol.mjs';
import { ROOT, exists } from './git.mjs';
import { gitRelay } from './git-relay.mjs';
import { requireWorkspace } from './workbench-state.mjs';
import { STATE, taskBinding, initializeTask, signalTask, withWorkbenchGitLock, withTaskControlLock, workbenchGit } from './workbench-task.mjs';
const task = taskBinding(process.argv.slice(2));
const home = await initializeTask(task);
const control = channel();
const childIds = new Set();
const flag = name => `${STATE}/task-${task.uid}.${name}`;
let worker, relay, config, stopping, stopped = false, closing = false;
const configuredLease = Number(process.env.PUPPYONE_WORKBENCH_TASK_LEASE_MS || 60000);
const leaseMilliseconds = Number.isFinite(configuredLease) ? Math.min(60000, Math.max(1000, configuredLease)) : 60000;
let leaseUntil = Date.now() + leaseMilliseconds;
process.umask(0o002);
setInterval(() => { if (Date.now() >= leaseUntil) close(); }, Math.min(1000, leaseMilliseconds / 4)).unref();

function liveTask(operation) {
  // Includes start and trusted Git, not just signals. A cleanup ACK cannot race
  // a late controller launching more processes or a queued metadata operation.
  return withTaskControlLock(task, async () => {
    if (await exists(flag('revoked'))) throw new Error('Task execution revoked');
    return operation();
  });
}
async function requireQuiescent() {
  if (await exists(flag('started')) && !await exists(flag('model-stopped')))
    throw new Error('Task model is still active');
}
async function quiesceModel() {
  stopping = (async () => {
    await signalTask(task, 'SIGSTOP'); await signalTask(task, 'SIGKILL');
    await writeFile(flag('model-stopped'), '', { mode: 0o600 }); stopped = true;
  })();
  try { await stopping; } finally { stopping = undefined; }
}
async function prepareWorkspace(value) {
  if (value.restore || value.resume) throw new Error('A task cannot restore a shared workbench');
  return liveTask(async () => {
    await requireQuiescent(); config = undefined;
    const prepared = await withWorkbenchGitLock(async () => {
      await requireWorkspace(task);
      relay?.close(); relay = await gitRelay(control.request, value.project_id, { authenticated: true });
      // Creation/clone belongs exclusively to the lifecycle controller.
      return workbenchGit(task, 'inspect', value);
    });
    config = value;
    return prepared;
  });
}
function start(value) {
  return liveTask(async () => {
    if (!config || worker || stopped || await exists(flag('started')) || await exists(flag('model-stopped')))
      throw new Error('Task is not startable');
    await writeFile(flag('started'), '', { flag: 'wx', mode: 0o600 });
    // Drop UID/GID before exec, not in a root setpriv launcher that might switch
    // identities after cleanup scanned the task UID. libuv also clears groups.
    worker = spawn('setpriv', ['--no-new-privs', '--inh-caps=-all', '--ambient-caps=-all',
      'node', '/opt/puppyone-agent/worker.mjs'], {
      cwd: ROOT, uid: task.uid, gid: 1000, stdio: ['pipe', 'pipe', 'pipe'],
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
  });
}
function stop() {
  return withTaskControlLock(task, async () => {
    await writeFile(flag('revoked'), '', { mode: 0o600 });
    await quiesceModel();
  });
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
        if (frame.action === 'capabilities') result = { shared_workbench: 2 };
        else if (frame.action === 'prepare') result = await prepareWorkspace(frame.value);
        else if (frame.action === 'stop' || frame.action === 'park') { await stop(); result = {}; }
        else if (frame.action === 'quiesce') { await liveTask(quiesceModel); result = {}; }
        else if (frame.action === 'inspect') result = await liveTask(async () => {
          await requireQuiescent();
          return withWorkbenchGitLock(() => workbenchGit(task, 'inspect', config));
        });
        else if (frame.action === 'finalize') result = await liveTask(async () => {
          await quiesceModel();
          return withWorkbenchGitLock(() => config.readonly
            ? workbenchGit(task, 'inspect', config).then(state => ({ ...state, changed: false, automatic_commit: false }))
            : workbenchGit(task, 'finalize', config, frame.value));
        });
        else if (frame.action === 'push') result = await liveTask(async () => {
          if (!stopped) throw new Error('Task must stop before publication');
          return withWorkbenchGitLock(() => relay.run(() => workbenchGit(task, 'push', config, frame.value, relay)));
        });
        else throw new Error('Operation belongs to workbench lifecycle, not a task');
        control.send({ type: 'control_result', id: frame.id, result });
        if (frame.action === 'park') { relay?.close(); setImmediate(() => process.exit(0)); }
      } catch (error) { control.send({ type: 'control_result', id: frame.id, error: error.message }); }
    });
  } else if (frame.type === 'start') start(frame.config).catch(error => control.send({ type: 'failed', error: error.message }));
  else if (worker && !stopped && !stopping && (frame.type !== 'reply' || childIds.has(frame.id))) {
    childIds.delete(frame.id); worker.stdin.write(JSON.stringify(frame) + '\n');
  }
});
process.stdin.on('end', close);
