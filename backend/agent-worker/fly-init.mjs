/** Root-owned Fly boot: disk layout and sealed task egress before SSH admission. */
import { mkdir, chmod } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
for (const tool of ['iptables', 'ip6tables']) {
  // SSH/provider control runs as root. Native tasks run as UID 1000.
  // Loopback carries only per-task model and Git relays. Deny both IP families.
  for (const args of [
    ['-A', 'OUTPUT', '-m', 'owner', '--uid-owner', '1000', '-d', tool === 'iptables' ? '127.0.0.0/8' : '::1/128', '-j', 'ACCEPT'],
    ['-A', 'OUTPUT', '-m', 'owner', '--uid-owner', '1000', '-j', 'REJECT'],
  ]) execFileSync(tool, args, {stdio: 'ignore'});
}
await mkdir('/data/workspace', {recursive: true});
await mkdir('/data/control', {recursive: true});
await chmod('/data', 0o755); await chmod('/data/control', 0o700);
// The image has an empty /workspace directory. Mount the persisted workspace
// through a bind mount so the normal no-symlink FS root contract is unchanged.
execFileSync('mount', ['--bind', '/data/workspace', '/workspace']);
await mkdir('/run/puppyone-workbench', {recursive: true});
execFileSync('mount', ['--bind', '/data/control', '/run/puppyone-workbench']);
setInterval(() => {}, 3600000);
