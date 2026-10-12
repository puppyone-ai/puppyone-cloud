/** Trusted task identity and short Git-operation lock for a shared computer. */
import { spawn } from 'node:child_process';
import { mkdir, chown, chmod, readFile, writeFile } from 'node:fs/promises';

export function taskIdentity(args) {
  if (!args.length) return null;
  const [uidFlag, uid, idFlag, id, generationFlag, generation] = args;
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (args.length !== 6 || uidFlag !== '--task-uid' || idFlag !== '--workbench' ||
      generationFlag !== '--generation' || !uuid.test(id) || !uuid.test(generation) ||
      !/^\d+$/.test(uid) || Number(uid) < 20000 || Number(uid) >= 60000)
    throw new Error('Invalid trusted workbench task binding');
  return { uid: Number(uid), id, generation };
}

export async function taskHome(task) {
  if (!task) return '/home/node';
  const home = `/workspace/.agents/${task.uid}`;
  await mkdir(home, {recursive:true});
  await chown(home, task.uid, 1000);
  await chmod(home, 0o700);
  return home;
}

export async function withGitLock(task, operation) {
  if (!task) return operation();
  // Kernel flock releases on process/connection death. Only Git metadata
  // operations serialize; ordinary user/Agent file writes continue freely.
  const lock = spawn('flock', ['-x', '-w', '120', '/recovery/workbench-git.lock',
    'node', '-e', 'process.stdout.write("ready\\n"); process.stdin.resume();'],
    {stdio:['pipe','pipe','pipe']});
  try {
    await new Promise((resolve,reject) => {
      lock.once('error',reject);
      lock.once('exit',code=>reject(new Error(`Workbench Git lock failed: ${code}`)));
      lock.stdout.once('data',resolve);
    });
    return await operation();
  } finally {
    lock.stdin.end();
    if (lock.exitCode === null) await new Promise(resolve=>lock.once('exit',resolve));
  }
}

export async function prepared(task) {
  try {
    const value = JSON.parse(await readFile('/recovery/workbench.json','utf8'));
    if (value.id !== task.id || value.generation !== task.generation)
      throw new Error('Workbench resource generation mismatch');
    return true;
  } catch (error) {
    if (error.code === 'ENOENT') return false;
    throw error;
  }
}

export async function markPrepared(task) {
  await writeFile('/recovery/workbench.json', JSON.stringify({id:task.id,generation:task.generation}),
    {flag:'wx', mode:0o600});
}
