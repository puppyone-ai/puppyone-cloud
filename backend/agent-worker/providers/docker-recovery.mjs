/** Docker's root-private recovery volume. Never enabled in E2B sandboxes. */
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { chmod, lstat, mkdir } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import path from 'node:path';

const exec = promisify(execFile);
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;

export function dockerRecovery(root, directory = '/recovery') {
  let lastSnapshot;
  async function location(id) {
    if (!UUID.test(id)) throw new Error('Invalid recovery identity');
    const destination = path.join(directory, id);
    const stat = await lstat(destination);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Invalid recovery directory');
    return destination;
  }
  return {
    async initialize() {
      await mkdir(directory, { recursive: true });
      const stat = await lstat(directory);
      if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Invalid recovery volume');
      await chmod(directory, 0o700);
    },
    // The controller freezes writers before invoking snapshot/restore.
    async snapshot() {
      const id = randomUUID();
      const destination = path.join(directory, id);
      await mkdir(destination);
      await exec('rsync', ['-a', ...(lastSnapshot ? [`--link-dest=${await location(lastSnapshot)}`] : []),
        '--', root + '/', destination + '/'], { timeout: 120000, maxBuffer: 8192 });
      lastSnapshot = id;
      return { id };
    },
    async restore(id) {
      const source = await location(id);
      await exec('rsync', ['-a', '--delete', '--', source + '/', root + '/'],
        { timeout: 120000, maxBuffer: 8192 });
      lastSnapshot = id;
    },
  };
}
