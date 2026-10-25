/** Sprites Service: persistent working/control directories, sealed task egress. */
import { mkdir, chmod, chown, lstat, readFile, writeFile } from 'node:fs/promises';
import { execFileSync, spawnSync } from 'node:child_process';
import { superviseFiles } from './workbench-files-supervisor.mjs';

if (process.getuid() !== 0) throw new Error('Sprites service requires root');
// The user task already runs with no_new_privs. Also protect the Sprite's
// management socket: workspace code must not create holds or manage services.
// chmod alone cannot isolate a socket owned by UID 1000. Its parent must also
// prevent that UID from replacing the socket after this check.
const managementDirectory = await lstat('/.sprite');
if (!managementDirectory.isDirectory() || managementDirectory.uid !== 0 || (managementDirectory.mode & 0o022))
  throw new Error('Sprites management directory must be root-owned and not user-writable');
if (!(await lstat('/.sprite/api.sock')).isSocket())
  throw new Error('Sprites management endpoint must be a Unix socket');
await chown('/.sprite/api.sock', 0, 0);
await chmod('/.sprite/api.sock', 0o600);
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
