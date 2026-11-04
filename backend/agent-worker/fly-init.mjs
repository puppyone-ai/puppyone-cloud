/** Root-owned Fly boot: disk layout and sealed task egress before SSH admission. */
import { mkdir, chmod, readFile, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { egressRules, PUBLIC_DNS } from './fly-egress.mjs';
import { superviseFiles } from './workbench-files-supervisor.mjs';
// Fly supports native nftables but not the iptables xt_owner kernel match.
// Install both IP families atomically, before admitting any task. SSH/provider
// control runs as root; UID 1000 reaches local model and Git relays, and the
// public internet only where the Workspace allows it.
// An unsupported firewall aborts boot, with diagnostics, never open egress.
const internet = process.env.PUPPYONE_TASK_INTERNET;
execFileSync('nft', ['-f', '-'], {
  input: egressRules(internet),
  stdio: ['pipe', 'inherit', 'inherit'],
});
// Fly's resolver sits on the private network the task user cannot reach.
if (internet === 'open') await writeFile('/etc/resolv.conf', PUBLIC_DNS);
await mkdir('/data/workspace', {recursive: true});
await mkdir('/data/control', {recursive: true});
await chmod('/data', 0o755); await chmod('/data/control', 0o700);
// The image has an empty /workspace directory. Mount the persisted workspace
// through a bind mount so the normal no-symlink FS root contract is unchanged.
execFileSync('mount', ['--bind', '/data/workspace', '/workspace']);
await mkdir('/run/puppyone-workbench', {recursive: true});
execFileSync('mount', ['--bind', '/data/control', '/run/puppyone-workbench']);
// Machine "started" precedes application readiness. A boot-specific receipt
// prevents SSH channels from racing disk mounts or trusting an earlier boot.
await superviseFiles();
await writeFile('/run/puppyone-fly-ready', await readFile('/proc/sys/kernel/random/boot_id'), {mode: 0o600});
