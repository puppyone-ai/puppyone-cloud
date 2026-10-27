/** Trusted shell controller: no Loop, publication or VM lifecycle ownership. */
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { writeFile } from 'node:fs/promises';
import { channel } from './protocol.mjs';
import { ROOT, exists } from './git.mjs';
import { STATE, taskBinding, initializeTask, signalTask, withTaskControlLock } from './workbench-task.mjs';

const task = taskBinding(process.argv.slice(2));
const home = await initializeTask(task);
const flag = name => `${STATE}/task-${task.uid}.${name}`;
let child, closed = false, stopping, operations = Promise.resolve();
let leaseUntil = Date.now() + 30000;
const control = channel(process.stdin, process.stdout, {
  maxFrame: 128 * 1024, maxBuffered: 1024 * 1024, onError: () => close(),
});
process.umask(0o002);
setInterval(() => { if (Date.now() >= leaseUntil) close(); }, 1000).unref();

async function stop() {
  return withTaskControlLock(task, async () => {
    await writeFile(flag('revoked'), '', { mode: 0o600 });
    await signalTask(task, 'SIGSTOP');
    await signalTask(task, 'SIGKILL');
  });
}
function close() {
  if (closed) return; closed = true;
  stopping = stop().finally(() => process.exit(0));
  stopping.catch(() => {}); // no ACK on an unconfirmed stop
}
function send(frame) { try { control.send(frame); } catch { close(); } }
async function start(value) {
  return withTaskControlLock(task, async () => {
    if (closed || child || await exists(flag('started')) || await exists(flag('revoked')))
      throw new Error('Shell execution is not startable');
    if (typeof value?.command !== 'string' || value.command.length > 32768 ||
        typeof value.term !== 'string' || !/^[a-zA-Z0-9_.+-]{0,64}$/.test(value.term) ||
        !Array.isArray(value.size) || value.size.length !== 2 ||
        value.size.some(n => !Number.isInteger(n) || n < 1 || n > 1000)) throw new Error('Invalid terminal');
    await writeFile(flag('started'), '', { flag: 'wx', mode: 0o600 });
    child = spawn('setpriv', ['--no-new-privs', '--inh-caps=-all', '--ambient-caps=-all',
      'python3', '-u', '/opt/puppyone-agent/workbench-shell.py', Buffer.from(JSON.stringify(value)).toString('base64')], {
      cwd: ROOT, uid: 1000, gid: task.uid, stdio: ['pipe', 'pipe', 'pipe'],
      env: { PATH: '/usr/local/bin:/usr/bin:/bin', HOME: home, TERM: value.term || 'dumb',
        GIT_CONFIG_COUNT: '1', GIT_CONFIG_KEY_0: 'safe.directory', GIT_CONFIG_VALUE_0: ROOT },
    });
    child.stdin.on('error', close);
    child.stderr.on('data', () => {});
    child.on('error', close);
    const lines = createInterface({ input: child.stdout });
    lines.on('line', line => {
      try {
        if (line.length > 100000) throw new Error('Shell output too large');
        const frame = JSON.parse(line);
        if (!['shell_data', 'shell_exit'].includes(frame.type)) throw new Error('Invalid shell event');
        send(frame);
      } catch { close(); }
    });
    child.on('close', () => { send({ type: 'shell_closed' }); });
  });
}
control.dispatch(frame => {
  if (frame.type !== 'control') return close();
  if (frame.action === 'heartbeat') {
    if (closed) return close();
    leaseUntil = Date.now() + 30000;
    return send({ type: 'control_result', id: frame.id, result: {} });
  }
  operations = operations.then(async () => {
    try {
      let result = {};
      if (frame.action === 'capabilities') result = { workspace_shell: 1, workbench: task.workbench, generation: task.generation, execution: task.execution };
      else if (frame.action === 'stop') { await stop(); }
      else if (frame.action === 'start') { await start(frame.value); }
      else if (['input', 'resize', 'eof', 'signal'].includes(frame.action)) {
        if (closed || !child || await exists(flag('revoked'))) throw new Error('Shell is unavailable');
        if (frame.action === 'input' && (typeof frame.value?.data !== 'string' || frame.value.data.length > 65536)) throw new Error('Input too large');
        if (child.stdin.writableLength > 1024 * 1024) throw new Error('Shell input backlog exceeded');
        child.stdin.write(JSON.stringify({ action: frame.action, ...frame.value }) + '\n');
      } else throw new Error('Unsupported shell action');
      send({ type: 'control_result', id: frame.id, result });
    } catch (error) {
      send({ type: 'control_result', id: frame.id, error: error.message });
    }
  });
});
process.stdin.on('end', close);
process.on('SIGTERM', close);
process.on('SIGINT', close);
