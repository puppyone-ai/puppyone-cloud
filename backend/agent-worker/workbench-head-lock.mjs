/**
 * Root's half of "one Workspace, one branch": the file user cannot move HEAD.
 *
 * The immutable flag needs a capability the task user never has. It backs the
 * Git gate rather than replacing it: Git changes files before it writes HEAD,
 * so only the gate can refuse without leaving the directory half switched.
 */
import { execFile } from 'node:child_process';
import { lstat, readFile } from 'node:fs/promises';
import { promisify } from 'node:util';
import { ROOT } from './git.mjs';
import { STATE } from './workbench-state.mjs';

const run = promisify(execFile);
const HEAD = `${ROOT}/.git/HEAD`;

async function plainHead() {
  try {
    return (await lstat(`${ROOT}/.git`)).isDirectory() && (await lstat(HEAD)).isFile();
  } catch (error) {
    if (error.code === 'ENOENT') return false;
    throw error;
  }
}

/** False when HEAD is not on the Workspace's branch; failing to lock always throws. */
export async function lockHead(target) {
  if (process.getuid() !== 0) throw new Error('Locking HEAD requires platform control');
  if (!(await plainHead())) return false;
  if ((await readFile(HEAD, 'utf8')).trimEnd() !== `ref: ${target}`) return false;
  await run('chattr', ['+i', HEAD]);
  const { stdout } = await run('lsattr', ['-d', HEAD]);
  if (!stdout.trim().split(/\s+/)[0].includes('i')) throw new Error('Workspace HEAD did not lock');
  return true;
}

/**
 * Every boot, before anything can connect. A machine whose HEAD already left
 * its branch must still start, so its owner can reach the work; it stays unlocked.
 */
export async function relockHead() {
  let intent;
  try {
    await lstat(`${STATE}/binding.json`);
    intent = JSON.parse(await readFile(`${STATE}/initialization.json`, 'utf8'));
  } catch (error) {
    if (error.code === 'ENOENT') return; // Not initialized yet: initialization locks it.
    throw error;
  }
  if (!(await lockHead(intent.target))) console.warn('workspace_head_unlocked');
}
