import test from 'node:test';
import assert from 'node:assert/strict';
import { mountWorkspace, resolveMountPlan } from './fly-mount.mjs';

test('default mount mode bind-mounts local NVMe /data/workspace onto /workspace', () => {
  const calls = [];
  const plan = mountWorkspace({}, (binary, args) => calls.push([binary, args]));
  assert.equal(plan.mode, 'local');
  assert.equal(plan.implemented, true);
  assert.deepEqual(calls, [['mount', ['--bind', '/data/workspace', '/workspace']]]);
});

test('fly_host_export prepares /workspace and /workspace/repo mountpoint at boot', () => {
  const calls = [];
  const plan = mountWorkspace(
    {
      PUPPYONE_WORKSPACE_MOUNT_MODE: 'fly_host_export',
      PUPPYONE_HOST_EXPORT_PROTOCOL: 'nfs_ganesha',
      PUPPYONE_HOST_EXPORT_TARGET: 'mach-123.vm.puppyone-ws.internal',
    },
    (binary, args) => calls.push([binary, args]),
  );
  assert.equal(plan.mode, 'fly_host_export');
  assert.equal(plan.protocol, 'nfs_ganesha');
  assert.equal(plan.implemented, true);
  assert.deepEqual(calls, [
    ['mount', ['--bind', '/data/workspace', '/workspace']],
    ['mkdir', ['-p', '/workspace/repo']],
    ['chmod', ['0755', '/workspace/repo']],
  ]);

  // Target may also be omitted at boot when lifecycle delivers the live host IP.
  const withoutTarget = resolveMountPlan({
    PUPPYONE_WORKSPACE_MOUNT_MODE: 'fly_host_export',
  });
  assert.equal(withoutTarget.protocol, 'nfs_ganesha');
  assert.equal(withoutTarget.implemented, true);

  assert.throws(
    () => resolveMountPlan({
      PUPPYONE_WORKSPACE_MOUNT_MODE: 'fly_host_export',
      PUPPYONE_HOST_EXPORT_PROTOCOL: 'smb',
      PUPPYONE_HOST_EXPORT_TARGET: 'mach-123.vm.puppyone-ws.internal',
    }),
    /Unsupported fly_host_export protocol/,
  );

  assert.throws(
    () => resolveMountPlan({
      PUPPYONE_WORKSPACE_MOUNT_MODE: 'fly_host_export',
      PUPPYONE_HOST_EXPORT_PROTOCOL: 'sshfs',
      PUPPYONE_HOST_EXPORT_TARGET: 'bad host; rm -rf /',
    }),
    /PUPPYONE_HOST_EXPORT_TARGET contains invalid characters/,
  );
});

test('juicefs_shared validates schema and root-only meta URL path and fails closed', () => {
  const plan = resolveMountPlan({
    PUPPYONE_WORKSPACE_MOUNT_MODE: 'juicefs_shared',
    PUPPYONE_JUICEFS_SCHEMA: 'jfs_proj_123',
    PUPPYONE_JUICEFS_META_URL_FILE: '/run/puppyone-workbench/juicefs-meta.url',
  });
  assert.equal(plan.mode, 'juicefs_shared');
  assert.equal(plan.schema, 'jfs_proj_123');
  assert.equal(plan.implemented, false);

  assert.throws(
    () => resolveMountPlan({
      PUPPYONE_WORKSPACE_MOUNT_MODE: 'juicefs_shared',
      PUPPYONE_JUICEFS_SCHEMA: 'jfs_proj_123',
      PUPPYONE_JUICEFS_META_URL_FILE: '/tmp/leaked.url',
    }),
    /must live under \/run\/puppyone-workbench\//,
  );
});

test('unknown mount mode fails closed', () => {
  assert.throws(
    () => resolveMountPlan({ PUPPYONE_WORKSPACE_MOUNT_MODE: 'daytona_s3' }),
    /Unsupported workspace mount mode/,
  );
});
