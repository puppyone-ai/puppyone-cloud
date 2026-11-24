import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  NFS_PORT,
  TUNNEL_SSH_PORT,
  detectFlyPrivateIpv6,
  formatKnownHostsLine,
  isFlyPrivateIpv6,
  parseMountTable,
  parsePeerSshdPids,
  removeAuthorizedKey,
  renderAuthorizedKeyLine,
  renderGaneshaConfig,
  renderSshdConfig,
  upsertAuthorizedKeys,
  validateAgentId,
  validateEd25519PublicKey,
  validateProtocol,
} from './workbench-host-export.mjs';

const VALID_KEY_A = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOV/sharedagentkeyAsharedagentkeyAshareda';
const VALID_KEY_B = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOV/sharedagentkeyBsharedagentkeyBsharedb';
const HOST_IP = 'fdaa:d9:cf93:a7b:354:4af9:aad4:2';
const AGENT_IP_A = 'fdaa:d9:cf93:a7b:354:aaaa:bbbb:2';

test('Fly 6pn IPv6 validator accepts only fdaa::/16 addresses', () => {
  assert.equal(isFlyPrivateIpv6(HOST_IP), true);
  assert.equal(isFlyPrivateIpv6('fdaa::1'), true);
  assert.equal(isFlyPrivateIpv6('127.0.0.1'), false);
  assert.equal(isFlyPrivateIpv6('8.8.8.8'), false);
  assert.equal(isFlyPrivateIpv6('2001:db8::1'), false);
  assert.equal(isFlyPrivateIpv6('fe80::1'), false);
  assert.equal(isFlyPrivateIpv6('fdaa::1; rm -rf /'), false);
});

test('detectFlyPrivateIpv6 extracts non-internal Fly 6pn interface address', () => {
  const detected = detectFlyPrivateIpv6({
    lo: [{ family: 'IPv6', internal: true, address: '::1' }],
    eth0: [
      { family: 'IPv4', internal: false, address: '172.19.1.2' },
      { family: 'IPv6', internal: false, address: HOST_IP },
    ],
  });
  assert.equal(detected, HOST_IP);
  assert.equal(detectFlyPrivateIpv6({ lo: [] }), null);
});

test('validators reject malformed keys, agent ids, and protocols', () => {
  assert.equal(validateEd25519PublicKey(`${VALID_KEY_A} comment@host`), VALID_KEY_A);
  assert.throws(() => validateEd25519PublicKey('ssh-rsa AAAA'), /Invalid Ed25519/);
  assert.throws(() => validateEd25519PublicKey(`${VALID_KEY_A}\ncommand="id"`), /Invalid Ed25519/);
  assert.equal(validateAgentId('agent_01-abc'), 'agent_01-abc');
  assert.throws(() => validateAgentId('../escape'), /Invalid agent_id/);
  assert.equal(validateProtocol('nfs_ganesha'), 'nfs_ganesha');
  assert.equal(validateProtocol('sshfs'), 'sshfs');
  assert.throws(() => validateProtocol('smb'), /Unsupported fly_host_export protocol/);
});

test('ganesha config binds strictly to 127.0.0.1 and squashes to UID/GID 1000', () => {
  const cfg = renderGaneshaConfig();
  assert.match(cfg, /Bind_addr = 127\.0\.0\.1;/);
  assert.match(cfg, new RegExp(`NFS_Port = ${NFS_PORT};`));
  assert.match(cfg, /NFS_Protocols = 4;/);
  assert.match(cfg, /Path = "\/workspace\/repo";/);
  assert.match(cfg, /Squash = All_Squash;/);
  assert.match(cfg, /Anonymous_uid = 1000;/);
  assert.match(cfg, /Anonymous_gid = 1000;/);
});

test('sshd config binds strictly to Fly 6pn IPv6 and enforces protocol-scoped confinement', () => {
  const nfsCfg = renderSshdConfig({
    protocol: 'nfs_ganesha',
    hostIp: HOST_IP,
    port: TUNNEL_SSH_PORT,
    hostKeyPath: '/run/puppyone-workbench/export/keys/ssh_host_ed25519_key',
    authorizedKeysPath: '/run/puppyone-workbench/export/authorized_keys_nfs_ganesha',
    pidFile: '/run/puppyone-workbench/export/sshd_nfs_ganesha.pid',
  });
  assert.match(nfsCfg, new RegExp(`ListenAddress \\[${HOST_IP}\\]`));
  assert.match(nfsCfg, /AllowTcpForwarding local/);
  assert.match(nfsCfg, /PermitOpen 127\.0\.0\.1:2049/);
  assert.match(nfsCfg, /ForceCommand \/bin\/false/);
  assert.match(nfsCfg, /PasswordAuthentication no/);

  const sftpCfg = renderSshdConfig({
    protocol: 'sshfs',
    hostIp: HOST_IP,
    port: 2222,
    hostKeyPath: '/run/puppyone-workbench/export/keys/ssh_host_ed25519_key',
    authorizedKeysPath: '/run/puppyone-workbench/export/authorized_keys_sshfs',
    pidFile: '/run/puppyone-workbench/export/sshd_sshfs.pid',
  });
  assert.match(sftpCfg, /ForceCommand internal-sftp -d \/workspace\/repo/);
  assert.match(sftpCfg, /AllowTcpForwarding no/);

  assert.throws(
    () => renderSshdConfig({ protocol: 'nfs_ganesha', hostIp: '0.0.0.0', port: 2223, hostKeyPath: 'k', authorizedKeysPath: 'a', pidFile: 'p' }),
    /Fly 6pn/,
  );
});

test('authorized_keys upsert & revoke isolate individual agents and pin from=<agent_ip>', () => {
  let content = '';
  content = upsertAuthorizedKeys(content, {
    agentId: 'wb-a',
    publicKey: VALID_KEY_A,
    protocol: 'nfs_ganesha',
    agentIp: AGENT_IP_A,
  });
  content = upsertAuthorizedKeys(content, {
    agentId: 'wb-b',
    publicKey: VALID_KEY_B,
    protocol: 'nfs_ganesha',
  });
  assert.match(content, new RegExp(`from="${AGENT_IP_A}",permitopen="127\\.0\\.0\\.1:2049".*# agent:wb-a`));
  assert.match(content, /permitopen="127\.0\.0\.1:2049".*# agent:wb-b/);

  // Re-granting wb-a replaces its line without duplicating.
  content = upsertAuthorizedKeys(content, {
    agentId: 'wb-a',
    publicKey: VALID_KEY_A,
    protocol: 'nfs_ganesha',
    agentIp: AGENT_IP_A,
  });
  assert.equal(content.trim().split('\n').length, 2);

  // Revoking ONLY wb-a leaves wb-b intact.
  const afterRevoke = removeAuthorizedKey(content, 'wb-a');
  assert.ok(!afterRevoke.includes('# agent:wb-a'));
  assert.ok(afterRevoke.includes('# agent:wb-b'));

  // SSHFS key line disables port forwarding.
  const sshfsLine = renderAuthorizedKeyLine({
    agentId: 'wb-c',
    publicKey: VALID_KEY_A,
    protocol: 'sshfs',
  });
  assert.match(sshfsLine, /^no-port-forwarding,no-pty/);
});

test('known_hosts formatting, mount table parsing, and peer sshd PID extraction', () => {
  assert.equal(
    formatKnownHostsLine(HOST_IP, 2223, VALID_KEY_A),
    `[${HOST_IP}]:2223 ${VALID_KEY_A}\n`,
  );
  const mounts = parseMountTable(
    `/dev/vda /data ext4 rw 0 0\n127.0.0.1:/repo /workspace/repo nfs4 rw,vers=4.1 0 0\n`,
    '/workspace/repo',
  );
  assert.equal(mounts.length, 1);
  assert.equal(mounts[0].fstype, 'nfs4');

  const ssOut = [
    `ESTAB 0 0 [${HOST_IP}]:2223 [${AGENT_IP_A}]:44112 users:(("sshd",pid=4102,fd=4),("sshd",pid=4101,fd=4))`,
    `ESTAB 0 0 [${HOST_IP}]:2223 [fdaa:d9:cf93:a7b:354:cccc:dddd:2]:55000 users:(("sshd",pid=5200,fd=4))`,
  ].join('\n');
  assert.deepEqual(parsePeerSshdPids(ssOut, AGENT_IP_A).sort(), [4101, 4102]);
});
