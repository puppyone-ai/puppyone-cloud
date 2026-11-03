/** Platform control state must not sit under a directory owned by the file user. */
import { mkdir, lstat, chmod, readFile } from 'node:fs/promises';
export const CONTROL_ROOT = '/run/puppyone-workbench';
export const STATE = `${CONTROL_ROOT}/control`;
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/**
 * The identity a Workspace gives the processes of one command or Agent run: the
 * GID they run under and are stopped by. 20000-59999 first, then 100000 onward,
 * so no command ever shares a group the image already has (nogroup is 65534).
 * Two billion lasts a machine decades at a command a second.
 */
export const TASK_UID_RANGES = [[20000, 59999], [100000, 2000000000]];
export function validTaskUid(value) {
  return Number.isSafeInteger(value) && TASK_UID_RANGES.some(([low, high]) => value >= low && value <= high);
}

export async function initializeControlState() {
  if (process.getuid() !== 0) throw new Error('Workspace lifecycle requires platform control');
  const parent = await lstat('/run');
  if (!parent.isDirectory() || parent.uid !== 0 || parent.mode & 0o022)
    throw new Error('Unprotected workspace control parent');
  for (const [directory, mode] of [[CONTROL_ROOT, 0o711], [STATE, 0o700]]) {
    try { await mkdir(directory, { mode }); } catch (error) { if (error.code !== 'EEXIST') throw error; }
    const info = await lstat(directory);
    if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== 0)
      throw new Error('Unprotected workspace control state');
    await chmod(directory, mode);
  }
}

export async function requireWorkspace(binding) {
  let saved;
  try { saved = JSON.parse(await readFile(`${STATE}/binding.json`, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') throw new Error('Workbench not initialized'); throw error; }
  if (saved.workbench !== binding.workbench || saved.generation !== binding.generation)
    throw new Error('Workbench generation mismatch');
}
