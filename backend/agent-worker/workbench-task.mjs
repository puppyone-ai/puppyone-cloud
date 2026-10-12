/** Trusted per-execution identity. No task owns VM pause, snapshot or deletion. */
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, chmod, chown, readFile, writeFile } from 'node:fs/promises';
const exec = promisify(execFile);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const CONTROL_ROOT = '/tmp/puppyone-workbench';
export const STATE = `${CONTROL_ROOT}/control`;

export function taskBinding(args) {
  const [uid, workbench, generation, execution] = args;
  if (args.length !== 4 || !/^\d+$/.test(uid) || Number(uid) < 20000 || Number(uid) > 59999 ||
      ![workbench, generation, execution].every(value => UUID.test(value)))
    throw new Error('Invalid admitted workbench task');
  return { uid: Number(uid), workbench, generation, execution };
}

export async function initializeTask(task) {
  // All roots are initialized by trusted control before any model starts.
  // Live control state is NOT a provider recovery copy or disposable scratch.
  await mkdir(CONTROL_ROOT, { recursive: true }); await chmod(CONTROL_ROOT, 0o711);
  await mkdir(STATE, { recursive: true }); await chmod(STATE, 0o700);
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

export async function withWorkbenchGitLock(operation) {
  // Only service Git metadata commands serialize. Tool/user filesystem writes do not.
  const lock = spawn('flock', ['-x', '-w', '120', `${STATE}/git.lock`,
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

export async function workbenchGit(action, config, value, relay) {
  return new Promise((resolve, reject) => {
    const child = execFile('node', ['/opt/puppyone-agent/workbench-git.mjs'], {
      uid: 1000, gid: 1000, timeout: 120000, maxBuffer: 1024 * 1024,
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
