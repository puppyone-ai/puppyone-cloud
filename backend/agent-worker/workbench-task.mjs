/** Trusted per-execution identity. No task owns VM pause, snapshot or deletion. */
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, chmod, chown, readFile, writeFile } from 'node:fs/promises';
const exec = promisify(execFile);
import { CONTROL_ROOT, STATE, UUID, requireWorkspace } from './workbench-state.mjs';
export { CONTROL_ROOT, STATE } from './workbench-state.mjs';

export function taskBinding(args) {
  const [uid, workbench, generation, execution] = args;
  if (args.length !== 4 || !/^\d+$/.test(uid) || Number(uid) < 20000 || Number(uid) > 59999 ||
      ![workbench, generation, execution].every(value => UUID.test(value)))
    throw new Error('Invalid admitted workbench task');
  return { uid: Number(uid), workbench, generation, execution };
}

export async function initializeTask(task) {
  // Tasks cannot initialize the workspace or its platform control roots.
  await requireWorkspace(task);
  const homes = `${CONTROL_ROOT}/homes`, home = `${homes}/${task.uid}`;
  await mkdir(homes, { recursive: true }); await chmod(homes, 0o711);
  await mkdir(home, { recursive: true }); await chown(home, task.uid, 1000); await chmod(home, 0o700);
  const name = `${STATE}/task-${task.uid}.json`;
  try { await writeFile(name, JSON.stringify(task), { flag: 'wx', mode: 0o600 }); }
  catch (error) {
    if (error.code !== 'EEXIST') throw error;
    if (await readFile(name, 'utf8') !== JSON.stringify(task)) throw new Error('Task UID is already owned');
  }
  return home;
}

export function withWorkbenchGitLock(operation) {
  // Only service Git metadata commands serialize. Tool/user filesystem writes do not.
  return withControlLock('git.lock', operation);
}

export function withTaskControlLock(task, operation) {
  return withControlLock(`stop-${task.uid}.lock`, operation);
}

async function withControlLock(name, operation) {
  const lock = spawn('flock', ['-x', '-w', '120', `${STATE}/${name}`,
    'node', '-e', 'process.stdout.write("ready\\n"); process.stdin.resume()'],
    { stdio: ['pipe', 'pipe', 'pipe'] });
  try {
    await new Promise((resolve, reject) => {
      lock.once('error', reject);
      lock.once('exit', () => reject(new Error('Workbench Git lock unavailable')));
      lock.stdout.once('data', resolve);
    });
    return await operation();
  } finally {
    lock.stdin.end();
    if (lock.exitCode === null) await new Promise(resolve => lock.once('exit', resolve));
  }
}

export function workbenchGit(task, action, config, value, relay) {
  return sharedGitCommand(task.uid, action, config, value, relay);
}

export async function sharedGitCommand(uid, action, config, value, relay) {
  return new Promise((resolve, reject) => {
    const child = execFile('node', ['/opt/puppyone-agent/workbench-git.mjs'], {
      // Lifecycle initialization uses the file user; task Git uses its captured process owner.
      uid, gid: 1000, timeout: 120000, maxBuffer: 1024 * 1024,
      env: { PATH: '/usr/local/bin:/usr/bin:/bin', HOME: '/home/node', ...relay?.transport },
    }, (error, stdout) => {
      if (error) reject(new Error(`Workbench Git ${action} failed`));
      else { try { resolve(JSON.parse(stdout)); } catch (cause) { reject(cause); } }
    });
    child.stdin.end(JSON.stringify({ action, config, value, remote: relay?.url }));
  });
}

export async function signalTask(task, signal) {
  if (!['SIGKILL', 'SIGSTOP', 'SIGCONT'].includes(signal)) throw new Error('Invalid task signal');
  // Signal as the target UID, NOT root: PID reuse between /proc read and kill
  // cannot signal another task or the ordinary user (the kernel enforces UID).
  await exec('node', ['/opt/puppyone-agent/workbench-signals.mjs', signal], {
    uid: task.uid, gid: 1000, timeout: 15000, maxBuffer: 8192,
    env: { PATH: '/usr/local/bin:/usr/bin:/bin' },
  });
}
