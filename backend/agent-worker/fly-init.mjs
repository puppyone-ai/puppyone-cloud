/** Root-owned Fly boot: disk layout and sealed task egress before SSH admission. */
import { mkdir, chmod, readFile, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
// Fly supports native nftables but not the iptables xt_owner kernel match.
// Install both IP families atomically, before admitting any task. SSH/provider
// control runs as root; UID 1000 may reach only local model and Git relays.
// An unsupported firewall aborts boot, with diagnostics, never open egress.
execFileSync('nft', ['-f', '-'], {
  input: `table inet puppyone_task_egress {
    chain output {
      type filter hook output priority 0; policy accept;
      meta skuid 1000 ip daddr 127.0.0.0/8 accept
      meta skuid 1000 ip6 daddr ::1/128 accept
      meta skuid 1000 counter reject with icmpx type admin-prohibited
    }
  }`,
  stdio: ['pipe', 'inherit', 'inherit'],
});
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
await writeFile('/run/puppyone-fly-ready', await readFile('/proc/sys/kernel/random/boot_id'), {mode: 0o600});
setInterval(() => {}, 3600000);
