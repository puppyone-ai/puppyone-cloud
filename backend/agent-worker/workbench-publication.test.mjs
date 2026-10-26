/** Real stock-Git transport on an owned loopback daemon; no provider or model. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { git, finalize, inspect, push } from './git.mjs';

async function daemon(directory) {
  const reservation = createServer(); reservation.listen(0, '127.0.0.1');
  await once(reservation, 'listening'); const { port } = reservation.address();
  await new Promise(resolve => reservation.close(resolve));
  const child = spawn('git', ['daemon', '--verbose', '--reuseaddr', '--export-all',
    '--enable=receive-pack', '--listen=127.0.0.1', `--port=${port}`, `--base-path=${directory}`, directory],
    { stdio: ['ignore', 'ignore', 'pipe'] });
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Owned Git daemon not ready')), 5000);
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('exit', code => { clearTimeout(timer); reject(new Error(`Git daemon exited: ${code}`)); });
      child.stderr.on('data', data => { if (String(data).includes('Ready to rumble')) { clearTimeout(timer); resolve(); } });
    });
    return { child, url: `git://127.0.0.1:${port}/remote.git` };
  } catch (error) { child.kill(); throw error; }
}

for (const format of ['sha1', 'sha256']) {
  test(`${format}: remote conflict retains both commits until explicit merge and push`, { timeout: 20000 }, async () => {
    const directory = await mkdtemp(path.join(tmpdir(), 'workbench-conflict-'));
    const root = path.join(directory, 'worktree'), remote = path.join(directory, 'remote.git');
    const other = path.join(directory, 'other');
    const config = { object_format: format, target_ref: 'refs/heads/workbench', base_oid: null };
    let server;
    try {
      await mkdir(root); await mkdir(remote);
      await git(root, ['init', `--object-format=${format}`, '--initial-branch=workbench', '.']);
      await git(remote, ['init', '--bare', `--object-format=${format}`, '.']);
      await writeFile(path.join(root, 'note'), 'shared starting point\n');
      const base = await finalize(root, config, 'Base');
      server = await daemon(directory);
      await push(root, config, server.url, base.tip);
      await git(directory, ['clone', '--branch=workbench', server.url, other]);
      await writeFile(path.join(other, 'note'), 'remote writer\n');
      const winner = await finalize(other, { ...config, base_oid: base.tip }, 'Remote writer');
      await push(other, config, server.url, winner.tip);
      // A later Run observes a new remote OID but never automatically pulls it.
      config.base_oid = winner.tip;
      await writeFile(path.join(root, 'note'), 'local writer\n');
      const local = await finalize(root, config, 'Local writer');
      assert.equal(local.automatic_commit, true);
      assert.equal((await push(root, config, server.url, local.tip)).rejected, true);
      assert.equal(await git(remote, ['rev-parse', config.target_ref]), winner.tip);
      assert.equal(await git(root, ['rev-parse', 'HEAD']), local.tip);
      assert.equal(await readFile(path.join(root, 'note'), 'utf8'), 'local writer\n');
      // Explicit user integration: fetch, resolve the content conflict, commit,
      // then publish the NEW candidate. The rejected candidate is not rewritten.
      await git(root, ['fetch', server.url, config.target_ref]);
      await assert.rejects(git(root, ['merge', '--no-edit', 'FETCH_HEAD']), error => error.code === 1);
      assert.ok(await git(root, ['ls-files', '--unmerged']));
      await writeFile(path.join(root, 'note'), 'local writer\nremote writer\n');
      await git(root, ['add', 'note']);
      await git(root, ['commit', '-m', 'Resolve both writers']);
      const merged = await git(root, ['rev-parse', 'HEAD']);
      await push(root, config, server.url, merged);
      assert.equal(await git(remote, ['rev-parse', config.target_ref]), merged);
      assert.equal(await git(remote, ['show', `${config.target_ref}:note`]), 'local writer\nremote writer');
      assert.equal(await git(root, ['show', `${local.tip}:note`]), 'local writer');
      assert.equal((await finalize(root, { ...config, base_oid: merged }, 'No changes')).automatic_commit, false);
    } finally {
      if (server && server.child.exitCode === null) { server.child.kill(); await once(server.child, 'exit'); }
      await rm(directory, { recursive: true, force: true });
    }
  });

  test(`${format}: original candidate can publish after another local commit without rewinding files`, { timeout: 20000 }, async () => {
    const directory = await mkdtemp(path.join(tmpdir(), 'workbench-publish-'));
    const root = path.join(directory, 'worktree'), remote = path.join(directory, 'remote.git');
    const config = { object_format: format, target_ref: 'refs/heads/workbench', base_oid: null };
    let server;
    try {
      await mkdir(root); await mkdir(remote);
      await git(root, ['init', `--object-format=${format}`, '--initial-branch=workbench', '.']);
      await git(remote, ['init', '--bare', `--object-format=${format}`, '.']);
      await git(remote, ['symbolic-ref', 'HEAD', 'refs/heads/main']);
      await inspect(root, config);
      await writeFile(path.join(root, 'note'), 'original candidate');
      const original = await finalize(root, config, 'Run A');
      await writeFile(path.join(root, 'note'), 'later task commit');
      const later = await finalize(root, config, 'Run B');
      await writeFile(path.join(root, 'note'), 'latest human unsaved content');
      server = await daemon(directory);
      await push(root, config, server.url, original.tip);
      assert.equal(await git(remote, ['rev-parse', config.target_ref]), original.tip);
      assert.equal(await git(remote, ['symbolic-ref', 'HEAD']), 'refs/heads/main');
      assert.equal(await git(remote, ['show', `${config.target_ref}:note`]), 'original candidate');
      assert.equal(await git(root, ['rev-parse', 'HEAD']), later.tip);
      assert.equal(await readFile(path.join(root, 'note'), 'utf8'), 'latest human unsaved content');
      await push(root, config, server.url, original.tip);
      for (const candidate of ['HEAD', '--all', 'a'.repeat(39)]) {
        await assert.rejects(push(root, config, server.url, candidate), /candidate/i);
      }
      await git(root, ['symbolic-ref', 'HEAD', 'refs/heads/other']);
      await assert.rejects(push(root, config, server.url, original.tip), /selected branch/);
    } finally {
      if (server && server.child.exitCode === null) { server.child.kill(); await once(server.child, 'exit'); }
      await rm(directory, { recursive: true, force: true });
    }
  });
}
