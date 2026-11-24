/**
 * Tier 2 (fly_host_export): Project Host VM exporter & Agent VM kernel-NFSv4/SSHFS mounter.
 *
 * Security invariants:
 * - Only root (via backend-authenticated workbench-lifecycle.mjs) invokes these actions.
 * - On the Project Host VM, nfs-ganesha binds strictly to 127.0.0.1:2049 (never 6pn/public).
 * - Host sshd listens strictly on the Fly 6pn private IPv6 (fdaa::/16) and accepts ONLY
 *   per-Agent Ed25519 keys restricted to `permitopen="127.0.0.1:2049"` + `ForceCommand /bin/false`
 *   (or `internal-sftp -d /workspace/repo` when protocol === 'sshfs').
 * - On the Agent VM, the client Ed25519 private key is generated in root-only 0700 storage and
 *   never leaves the VM; the Host VM's exact Ed25519 host key is pinned via StrictHostKeyChecking=yes.
 * - fly-egress.mjs blocks UID 1000 from reaching both fc00::/7 and 127.0.0.1:{2049,2222,2223}.
 */

import { execFile, spawn } from 'node:child_process';
import { constants as fsConstants } from 'node:fs';
import {
  chmod,
  lstat,
  mkdir,
  readFile,
  rename,
  rm,
  stat,
  symlink,
  writeFile,
} from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import { pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { ROOT } from './git.mjs';
import { STATE, UUID, initializeControlState } from './workbench-state.mjs';

const execFileAsync = promisify(execFile);

export const EXPORT_ROOT = '/run/puppyone-workbench/export';
export const EXPORT_KEYS_DIR = `${EXPORT_ROOT}/keys`;
export const NFS_PORT = 2049;
export const TUNNEL_SSH_PORT = 2223;
export const SFTP_SSH_PORT = 2222;
export const EXPORT_PSEUDO = '/repo';
export const SUPPORTED_PROTOCOLS = Object.freeze(['nfs_ganesha', 'sshfs']);

const ED25519_PUB_RE = /^ssh-ed25519 [A-Za-z0-9+/]{32,128}={0,2}(?: [^\r\n]{1,128})?$/;
const SAFE_AGENT_ID_RE = /^[a-zA-Z0-9._-]{1,128}$/;

export function isFlyPrivateIpv6(value) {
  if (typeof value !== 'string') return false;
  const trimmed = value.trim().toLowerCase();
  if (!trimmed.startsWith('fdaa:')) return false;
  return net.isIPv6(trimmed);
}

export function validateProtocol(protocol) {
  const value = (protocol || 'nfs_ganesha').trim();
  if (!SUPPORTED_PROTOCOLS.includes(value)) {
    throw new Error(`Unsupported fly_host_export protocol: ${value}`);
  }
  return value;
}

export function validateEd25519PublicKey(key) {
  if (typeof key !== 'string' || /[\r\n]/.test(key)) throw new Error('Invalid Ed25519 public key');
  const trimmed = key.trim();
  if (!ED25519_PUB_RE.test(trimmed)) {
    throw new Error('Invalid Ed25519 public key');
  }
  return trimmed.split(/[ \t]+/).slice(0, 2).join(' ');
}

export function validateAgentId(agentId) {
  if (typeof agentId !== 'string' || !SAFE_AGENT_ID_RE.test(agentId)) {
    throw new Error('Invalid agent_id');
  }
  return agentId;
}

export function detectFlyPrivateIpv6(interfaces = os.networkInterfaces()) {
  for (const entries of Object.values(interfaces || {})) {
    for (const entry of entries || []) {
      if (entry.family === 'IPv6' && !entry.internal && isFlyPrivateIpv6(entry.address)) {
        return entry.address;
      }
    }
  }
  return null;
}

export function renderGaneshaConfig({ exportPath = ROOT, pseudo = EXPORT_PSEUDO, port = NFS_PORT } = {}) {
  return `NFS_Core_Param {
    NFS_Port = ${port};
    Bind_addr = 127.0.0.1;
    NFS_Protocols = 4;
    Enable_NLM = false;
    Enable_RQUOTA = false;
    Enable_UDP = false;
}
NFSV4 {
    Graceless = true;
    Minor_Versions = 1, 2;
}
EXPORT {
    Export_Id = 10;
    Path = "${exportPath}";
    Pseudo = "${pseudo}";
    Access_Type = RW;
    Squash = All_Squash;
    Anonymous_uid = 1000;
    Anonymous_gid = 1000;
    Transports = TCP;
    Protocols = 4;
    SecType = sys;
    FSAL {
        Name = VFS;
    }
}
`;
}

export function renderSshdConfig({
  protocol,
  hostIp,
  port,
  hostKeyPath,
  authorizedKeysPath,
  pidFile,
  exportPath = ROOT,
}) {
  if (!isFlyPrivateIpv6(hostIp)) throw new Error('Host export requires a Fly 6pn fdaa:: IPv6');
  const proto = validateProtocol(protocol);
  const matchBlock =
    proto === 'sshfs'
      ? `Subsystem sftp internal-sftp
Match User node
  ForceCommand internal-sftp -d ${exportPath}
  AllowTcpForwarding no
  X11Forwarding no
  AllowAgentForwarding no
  PermitTTY no`
      : `Match User node
  AllowTcpForwarding local
  PermitOpen 127.0.0.1:${NFS_PORT}
  X11Forwarding no
  AllowAgentForwarding no
  PermitTTY no
  ForceCommand /bin/false`;
  return `Port ${port}
ListenAddress [${hostIp}]
HostKey ${hostKeyPath}
AuthorizedKeysFile ${authorizedKeysPath}
PidFile ${pidFile}
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
UsePAM no
StrictModes no
AllowUsers node
ClientAliveInterval 15
ClientAliveCountMax 3
${matchBlock}
`;
}

export function renderAuthorizedKeyLine({ agentId, publicKey, protocol = 'nfs_ganesha', agentIp = null }) {
  const safeId = validateAgentId(agentId);
  const key = validateEd25519PublicKey(publicKey);
  const proto = validateProtocol(protocol);
  const options =
    proto === 'sshfs'
      ? ['no-port-forwarding', 'no-pty', 'no-X11-forwarding', 'no-agent-forwarding']
      : [`permitopen="127.0.0.1:${NFS_PORT}"`, 'no-pty', 'no-X11-forwarding', 'no-agent-forwarding'];
  if (agentIp !== null && agentIp !== undefined && agentIp !== '') {
    if (!isFlyPrivateIpv6(agentIp)) throw new Error('agent_ip must be a Fly 6pn fdaa:: IPv6');
    options.unshift(`from="${agentIp.trim()}"`);
  }
  return `${options.join(',')} ${key} # agent:${safeId}`;
}

export function upsertAuthorizedKeys(existingContent, { agentId, publicKey, protocol, agentIp = null }) {
  const safeId = validateAgentId(agentId);
  const marker = `# agent:${safeId}`;
  const lines = (existingContent || '')
    .split('\n')
    .map(line => line.trim())
    .filter(line => line && !line.endsWith(marker));
  lines.push(renderAuthorizedKeyLine({ agentId: safeId, publicKey, protocol, agentIp }));
  return lines.join('\n') + '\n';
}

export function removeAuthorizedKey(existingContent, agentId) {
  const safeId = validateAgentId(agentId);
  const marker = `# agent:${safeId}`;
  const lines = (existingContent || '')
    .split('\n')
    .map(line => line.trim())
    .filter(line => line && !line.endsWith(marker));
  return lines.length ? lines.join('\n') + '\n' : '';
}

export function formatKnownHostsLine(hostIp, port, hostPublicKey) {
  if (!isFlyPrivateIpv6(hostIp)) throw new Error('host_ip must be a Fly 6pn fdaa:: IPv6');
  const key = validateEd25519PublicKey(hostPublicKey);
  const numericPort = Number(port);
  if (!Number.isInteger(numericPort) || numericPort < 1 || numericPort > 65535) {
    throw new Error('Invalid host export port');
  }
  return `[${hostIp.trim()}]:${numericPort} ${key}\n`;
}

export function parseMountTable(mtabContent, targetPath = ROOT) {
  const matches = [];
  for (const line of (mtabContent || '').split('\n')) {
    const parts = line.trim().split(/\s+/);
    if (parts.length >= 4 && parts[1] === targetPath) {
      matches.push({ source: parts[0], target: parts[1], fstype: parts[2], options: parts[3] });
    }
  }
  return matches;
}

export function parsePeerSshdPids(ssOutput, agentIp) {
  if (!isFlyPrivateIpv6(agentIp)) return [];
  const target = agentIp.trim().toLowerCase();
  const pids = new Set();
  for (const line of (ssOutput || '').split('\n')) {
    if (!line.toLowerCase().includes(target)) continue;
    for (const match of line.matchAll(/pid=(\d+)/g)) {
      const pid = Number(match[1]);
      if (Number.isInteger(pid) && pid > 1) pids.add(pid);
    }
  }
  return [...pids];
}

function requireRoot() {
  if (process.getuid() !== 0) throw new Error('Host export operations require platform root control');
}

async function ensureExportDirs() {
  await initializeControlState();
  await mkdir(EXPORT_ROOT, { recursive: true, mode: 0o711 });
  await chmod(EXPORT_ROOT, 0o711);
  await mkdir(EXPORT_KEYS_DIR, { recursive: true, mode: 0o700 });
  await chmod(EXPORT_KEYS_DIR, 0o700);
}

async function ensureMtab() {
  try {
    await lstat('/etc/mtab');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    await symlink('/proc/self/mounts', '/etc/mtab');
  }
}

async function readPid(pidFile) {
  try {
    const raw = (await readFile(pidFile, 'utf8')).trim();
    const pid = Number(raw);
    if (!Number.isInteger(pid) || pid <= 1) return null;
    process.kill(pid, 0);
    return pid;
  } catch {
    return null;
  }
}

function waitForTcp(host, port, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const socket = net.connect({ host, port });
      const done = ok => {
        socket.removeAllListeners();
        socket.destroy();
        if (ok) return resolve();
        if (Date.now() >= deadline) {
          return reject(new Error(`Timed out waiting for ${host}:${port}`));
        }
        setTimeout(attempt, 150);
      };
      socket.once('connect', () => done(true));
      socket.once('error', () => done(false));
      socket.setTimeout(1000, () => done(false));
    };
    attempt();
  });
}

async function ensureEd25519Keypair(keyPath, comment) {
  try {
    await stat(keyPath);
    await stat(`${keyPath}.pub`);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    await execFileAsync('ssh-keygen', ['-q', '-t', 'ed25519', '-N', '', '-C', comment, '-f', keyPath]);
    await chmod(keyPath, 0o600);
    await chmod(`${keyPath}.pub`, 0o644);
  }
  const pub = await readFile(`${keyPath}.pub`, 'utf8');
  return validateEd25519PublicKey(pub);
}

async function atomicWrite(filePath, content, mode) {
  const tmp = `${filePath}.tmp.${process.pid}.${Date.now()}`;
  await writeFile(tmp, content, { mode });
  await chmod(tmp, mode);
  await rename(tmp, filePath);
}

/**
 * Project Host VM: start/verify nfs-ganesha (127.0.0.1:2049) + per-Agent sshd ([6pn]:port).
 */
export async function startHostExport({ protocol = 'nfs_ganesha' } = {}) {
  requireRoot();
  const proto = validateProtocol(protocol);
  await ensureExportDirs();
  await ensureMtab();

  const hostIp = detectFlyPrivateIpv6();
  if (!hostIp) throw new Error('Fly 6pn private IPv6 address not found on host machine');

  await mkdir(ROOT, { recursive: true });
  await mkdir('/run/sshd', { recursive: true, mode: 0o755 });
  await mkdir('/var/run/ganesha', { recursive: true, mode: 0o755 });

  const hostKeyPath = `${EXPORT_KEYS_DIR}/ssh_host_ed25519_key`;
  const hostPublicKey = await ensureEd25519Keypair(hostKeyPath, 'puppyone-host-export');
  const authorizedKeysPath = `${EXPORT_ROOT}/authorized_keys_${proto}`;
  try {
    await stat(authorizedKeysPath);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    await atomicWrite(authorizedKeysPath, '', 0o644);
  }

  // 1. Start NFS-Ganesha bound strictly to 127.0.0.1:2049 when using nfs_ganesha.
  if (proto === 'nfs_ganesha') {
    const ganeshaPidFile = `${EXPORT_ROOT}/ganesha.pid`;
    const ganeshaRunning = await readPid(ganeshaPidFile);
    if (!ganeshaRunning) {
      const ganeshaConf = `${EXPORT_ROOT}/ganesha.conf`;
      const ganeshaLog = `${EXPORT_ROOT}/ganesha.log`;
      await atomicWrite(ganeshaConf, renderGaneshaConfig({ exportPath: ROOT }), 0o600);
      const child = spawn(
        '/usr/bin/ganesha.nfsd',
        ['-F', '-L', ganeshaLog, '-f', ganeshaConf],
        { detached: true, stdio: 'ignore' },
      );
      child.unref();
      await atomicWrite(ganeshaPidFile, `${child.pid}\n`, 0o600);
    }
    await waitForTcp('127.0.0.1', NFS_PORT, 15000);
  }

  // 2. Start dedicated sshd bound strictly to [host_6pn_ip]:port.
  const port = proto === 'sshfs' ? SFTP_SSH_PORT : TUNNEL_SSH_PORT;
  const sshdPidFile = `${EXPORT_ROOT}/sshd_${proto}.pid`;
  const sshdRunning = await readPid(sshdPidFile);
  if (!sshdRunning) {
    const sshdConf = `${EXPORT_ROOT}/sshd_${proto}.conf`;
    await atomicWrite(
      sshdConf,
      renderSshdConfig({
        protocol: proto,
        hostIp,
        port,
        hostKeyPath,
        authorizedKeysPath,
        pidFile: sshdPidFile,
        exportPath: ROOT,
      }),
      0o600,
    );
    await execFileAsync('/usr/sbin/sshd', ['-f', sshdConf]);
  }
  await waitForTcp(hostIp, port, 10000);

  return {
    protocol: proto,
    host_ip: hostIp,
    port,
    host_public_key: hostPublicKey,
    export_path: proto === 'sshfs' ? ROOT : EXPORT_PSEUDO,
  };
}

/**
 * Project Host VM: authorize one isolated Agent VM's ephemeral Ed25519 key.
 */
export async function grantHostExport({
  agent_id: agentId,
  public_key: publicKey,
  protocol = 'nfs_ganesha',
  agent_ip: agentIp = null,
} = {}) {
  requireRoot();
  const proto = validateProtocol(protocol);
  await ensureExportDirs();
  const authorizedKeysPath = `${EXPORT_ROOT}/authorized_keys_${proto}`;
  let existing = '';
  try {
    existing = await readFile(authorizedKeysPath, 'utf8');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const updated = upsertAuthorizedKeys(existing, {
    agentId,
    publicKey,
    protocol: proto,
    agentIp,
  });
  await atomicWrite(authorizedKeysPath, updated, 0o644);
  return { granted: true, agent_id: validateAgentId(agentId), protocol: proto };
}

/**
 * Project Host VM: immediately revoke one Agent VM's export key & sever its active tunnel.
 */
export async function revokeHostExport({
  agent_id: agentId,
  agent_ip: agentIp = null,
  protocol = null,
} = {}) {
  requireRoot();
  const safeId = validateAgentId(agentId);
  await ensureExportDirs();
  const protocols = protocol ? [validateProtocol(protocol)] : SUPPORTED_PROTOCOLS;
  for (const proto of protocols) {
    const authorizedKeysPath = `${EXPORT_ROOT}/authorized_keys_${proto}`;
    try {
      const existing = await readFile(authorizedKeysPath, 'utf8');
      const updated = removeAuthorizedKey(existing, safeId);
      await atomicWrite(authorizedKeysPath, updated, 0o644);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    if (agentIp && isFlyPrivateIpv6(agentIp)) {
      const port = proto === 'sshfs' ? SFTP_SSH_PORT : TUNNEL_SSH_PORT;
      try {
        const { stdout } = await execFileAsync('ss', ['-Htpn', `sport = :${port}`]);
        for (const pid of parsePeerSshdPids(stdout, agentIp)) {
          try {
            process.kill(pid, 'SIGKILL');
          } catch {
            // Already exited.
          }
        }
      } catch {
        // Best-effort live socket termination; authorized_keys removal prevents reconnect.
      }
    }
  }
  return { revoked: true, agent_id: safeId };
}

/**
 * Agent VM: generate/reuse root-only Ed25519 keypair and return ONLY its public key.
 */
export async function ensureClientKey() {
  requireRoot();
  await ensureExportDirs();
  const keyPath = `${EXPORT_KEYS_DIR}/client_key`;
  const publicKey = await ensureEd25519Keypair(keyPath, 'puppyone-agent-mount');
  return {
    public_key: publicKey,
    agent_ip: detectFlyPrivateIpv6(),
  };
}

async function currentRemoteMount() {
  const content = await readFile('/proc/self/mounts', 'utf8');
  return parseMountTable(content, ROOT).find(m => m.fstype === 'nfs4' || m.fstype === 'fuse.sshfs') || null;
}

async function stopTunnelSupervisor() {
  const pidFile = `${EXPORT_ROOT}/tunnel.pid`;
  const pid = await readPid(pidFile);
  if (pid) {
    try {
      process.kill(-pid, 'SIGTERM');
    } catch {
      try {
        process.kill(pid, 'SIGTERM');
      } catch {
        // Already exited.
      }
    }
    await new Promise(resolve => setTimeout(resolve, 250));
    try {
      process.kill(-pid, 'SIGKILL');
    } catch {
      try {
        process.kill(pid, 'SIGKILL');
      } catch {
        // Already exited.
      }
    }
  }
  await rm(pidFile, { force: true }).catch(() => {});
}

export async function unmountHostExport() {
  requireRoot();
  await ensureExportDirs();
  await stopTunnelSupervisor();
  if (await currentRemoteMount()) {
    try {
      await execFileAsync('umount', [ROOT], { timeout: 10000 });
    } catch {
      await execFileAsync('umount', ['-l', ROOT], { timeout: 10000 }).catch(() => {});
    }
  }
  await rm(`${STATE}/host-export-mount.json`, { force: true }).catch(() => {});
  return { unmounted: true };
}

/**
 * Agent VM: connect supervised SSH tunnel + mount Project Host VM's /workspace/repo.
 */
export async function mountHostExport({
  workbench,
  generation,
  protocol = 'nfs_ganesha',
  host_ip: hostIp,
  port,
  host_public_key: hostPublicKey,
  export_path: exportPath,
} = {}) {
  requireRoot();
  if (![workbench, generation].every(value => typeof value === 'string' && UUID.test(value))) {
    throw new Error('Invalid workspace lifecycle binding');
  }
  const proto = validateProtocol(protocol);
  if (!isFlyPrivateIpv6(hostIp)) throw new Error('host_ip must be a Fly 6pn fdaa:: IPv6');
  const numericPort = Number(port || (proto === 'sshfs' ? SFTP_SSH_PORT : TUNNEL_SSH_PORT));
  const verifiedHostKey = validateEd25519PublicKey(hostPublicKey);
  await ensureExportDirs();

  const spec = {
    protocol: proto,
    host_ip: hostIp.trim(),
    port: numericPort,
    host_public_key: verifiedHostKey,
    export_path: exportPath || (proto === 'sshfs' ? ROOT : EXPORT_PSEUDO),
  };

  // Fast idempotent check: if already mounted to the exact same host spec and healthy, keep it.
  const mountStatePath = `${STATE}/host-export-mount.json`;
  const existingMount = await currentRemoteMount();
  if (existingMount) {
    let savedSpec = null;
    try {
      savedSpec = JSON.parse(await readFile(mountStatePath, 'utf8'));
    } catch {
      // Missing state file -> remount cleanly.
    }
    const tunnelAlive = proto === 'sshfs' || Boolean(await readPid(`${EXPORT_ROOT}/tunnel.pid`));
    if (savedSpec && JSON.stringify(savedSpec) === JSON.stringify(spec) && tunnelAlive) {
      try {
        await stat(ROOT);
        await atomicWrite(`${STATE}/binding.json`, JSON.stringify({ workbench, generation }), 0o600);
        return { mounted: true, reused: true, protocol: proto, fstype: existingMount.fstype };
      } catch {
        // Stale mount -> tear down and remount below.
      }
    }
    await unmountHostExport();
  }

  const keyPath = `${EXPORT_KEYS_DIR}/client_key`;
  await stat(keyPath);
  const knownHostsPath = `${EXPORT_KEYS_DIR}/known_hosts`;
  await atomicWrite(knownHostsPath, formatKnownHostsLine(spec.host_ip, spec.port, verifiedHostKey), 0o600);
  await mkdir(ROOT, { recursive: true, mode: 0o755 });

  if (proto === 'nfs_ganesha') {
    const tunnelConfigPath = `${EXPORT_ROOT}/tunnel.json`;
    await atomicWrite(
      tunnelConfigPath,
      JSON.stringify({
        host_ip: spec.host_ip,
        port: spec.port,
        key_path: keyPath,
        known_hosts_path: knownHostsPath,
        local_port: NFS_PORT,
        remote_port: NFS_PORT,
      }),
      0o600,
    );
    const supervisor = spawn(
      process.execPath,
      [new URL('./workbench-host-export.mjs', import.meta.url).pathname, '--tunnel', tunnelConfigPath],
      { detached: true, stdio: 'ignore' },
    );
    supervisor.unref();
    await atomicWrite(`${EXPORT_ROOT}/tunnel.pid`, `${supervisor.pid}\n`, 0o600);
    await waitForTcp('127.0.0.1', NFS_PORT, 15000);

    try {
      await execFileAsync('modprobe', ['nfsv4']);
    } catch {
      // Already loaded or built-in.
    }
    await execFileAsync(
      'mount',
      [
        '-t',
        'nfs4',
        '-o',
        `proto=tcp,port=${NFS_PORT},vers=4.1,hard,timeo=50,retrans=3,actimeo=1,lookupcache=pos`,
        `127.0.0.1:${spec.export_path}`,
        ROOT,
      ],
      { timeout: 25000 },
    );
  } else {
    await execFileAsync(
      'sshfs',
      [
        '-p',
        String(spec.port),
        '-o',
        [
          `IdentityFile=${keyPath}`,
          'StrictHostKeyChecking=yes',
          `UserKnownHostsFile=${knownHostsPath}`,
          'IdentitiesOnly=yes',
          'allow_other',
          'default_permissions',
          'reconnect',
          'ServerAliveInterval=15',
          'ServerAliveCountMax=3',
          'cache_timeout=1',
        ].join(','),
        `node@[${spec.host_ip}]:${spec.export_path}`,
        ROOT,
      ],
      { timeout: 25000 },
    );
  }

  const verified = await currentRemoteMount();
  if (!verified) {
    await unmountHostExport();
    throw new Error(`Remote mount verification failed for ${ROOT}`);
  }
  await stat(ROOT);
  await atomicWrite(`${STATE}/binding.json`, JSON.stringify({ workbench, generation }), 0o600);
  await atomicWrite(mountStatePath, JSON.stringify(spec), 0o600);

  return { mounted: true, reused: false, protocol: proto, fstype: verified.fstype };
}

/**
 * Detached SSH tunnel supervisor (`--tunnel <config.json>`).
 * Keeps the root-owned loopback port-forward alive across transient 6pn blips.
 */
async function runTunnelSupervisor(configPath) {
  const cfg = JSON.parse(await readFile(configPath, 'utf8'));
  let stopping = false;
  let current = null;
  const shutdown = () => {
    stopping = true;
    current?.kill('SIGTERM');
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);

  while (!stopping) {
    const started = Date.now();
    await new Promise(resolve => {
      current = spawn(
        'ssh',
        [
          '-N',
          '-i',
          cfg.key_path,
          '-o',
          'StrictHostKeyChecking=yes',
          '-o',
          `UserKnownHostsFile=${cfg.known_hosts_path}`,
          '-o',
          'IdentitiesOnly=yes',
          '-o',
          'BatchMode=yes',
          '-o',
          'ExitOnForwardFailure=yes',
          '-o',
          'ServerAliveInterval=10',
          '-o',
          'ServerAliveCountMax=3',
          '-p',
          String(cfg.port),
          '-L',
          `127.0.0.1:${cfg.local_port}:127.0.0.1:${cfg.remote_port}`,
          `node@${cfg.host_ip}`,
        ],
        { stdio: 'ignore' },
      );
      current.once('exit', () => {
        current = null;
        resolve();
      });
      current.once('error', () => {
        current = null;
        resolve();
      });
    });
    if (stopping) break;
    const elapsed = Date.now() - started;
    await new Promise(resolve => setTimeout(resolve, elapsed > 10000 ? 300 : 1500));
  }
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href &&
  process.argv[2] === '--tunnel' &&
  process.argv[3]
) {
  await runTunnelSupervisor(process.argv[3]);
}
