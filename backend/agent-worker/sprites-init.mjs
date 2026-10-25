/** Sprites Service: persistent working/control directories, sealed task egress. */
import { mkdir, lstat, readFile, writeFile } from 'node:fs/promises';
import { execFileSync, spawnSync } from 'node:child_process';
import { superviseFiles } from './workbench-files-supervisor.mjs';

if (process.getuid() !== 0) throw new Error('Sprites service requires root');
// The platform socket is a read-only bind mount, so chmod/chown cannot seal it.
// Keep a root-only bind alias for lifecycle calls, then mask the public path.
// Tasks already have no_new_privs and cannot undo these mounts. Recreate them
// after a cold boot; preserve the alias on a service-only restart.
const managementDirectory = await lstat('/.sprite');
if (!managementDirectory.isDirectory() || managementDirectory.uid !== 0 || (managementDirectory.mode & 0o022))
  throw new Error('Sprites management directory must be root-owned and not user-writable');
const privateDirectory = '/run/puppyone-sprites';
await mkdir(privateDirectory, {recursive: true, mode: 0o700});
const privateStat = await lstat(privateDirectory);
if (!privateStat.isDirectory() || privateStat.uid !== 0 || (privateStat.mode & 0o077))
  throw new Error('Sprites private management directory must be root-only');
async function placeholder(path, mode) {
  try { await writeFile(path, '', {flag: 'wx', mode}); }
  catch (error) { if (error.code !== 'EEXIST') throw error; }
  const info = await lstat(path);
  if (!info.isFile() || info.uid !== 0 || (info.mode & 0o777) !== mode)
    throw new Error('Unsafe Sprites management mount target');
  return info;
}
const managementSocket = `${privateDirectory}/api.sock`;
if (spawnSync('mountpoint', ['-q', managementSocket]).status !== 0) {
  if (!(await lstat('/.sprite/api.sock')).isSocket())
    throw new Error('Sprites management endpoint must be a Unix socket');
  await placeholder(managementSocket, 0o600);
  execFileSync('mount', ['--bind', '/.sprite/api.sock', managementSocket]);
}
if (!(await lstat(managementSocket)).isSocket())
  throw new Error('Sprites private management endpoint must be a Unix socket');
const blocked = `${privateDirectory}/blocked`;
const mask = await placeholder(blocked, 0o000);
const exposed = await lstat('/.sprite/api.sock');
if (exposed.dev !== mask.dev || exposed.ino !== mask.ino)
  execFileSync('mount', ['--bind', blocked, '/.sprite/api.sock']);
const table = 'puppyone_task_egress';
const exists = spawnSync('nft', ['list', 'table', 'inet', table]).status === 0;
execFileSync('nft', ['-f', '-'], {
  input: `${exists ? `delete table inet ${table}\n` : ''}table inet ${table} {
    chain output {
      type filter hook output priority 0; policy accept;
      meta skuid 1000 ip daddr 127.0.0.0/8 accept
      meta skuid 1000 ip6 daddr ::1/128 accept
      meta skuid 1000 counter reject with icmpx type admin-prohibited
    }
  }`, stdio: ['pipe', 'inherit', 'inherit'],
});
// /run can be tmpfs after a cold wake. Bind the same durable control directory
// there so generation receipts and task state survive alongside working files.
for (const [source, target] of [
  ['/var/lib/puppyone/workspace', '/workspace'],
  ['/var/lib/puppyone/control', '/run/puppyone-workbench'],
]) {
  await mkdir(source, {recursive: true});
  await mkdir(target, {recursive: true});
  if (spawnSync('mountpoint', ['-q', target]).status !== 0)
    execFileSync('mount', ['--bind', source, target]);
}
await mkdir('/home/node/.pi', {recursive: true});
await superviseFiles();
await writeFile('/run/puppyone-sprites-ready', await readFile('/proc/sys/kernel/random/boot_id'), {mode: 0o600});
