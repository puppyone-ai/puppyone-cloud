import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, mkdir, writeFile, readFile, lstat, readdir, rm, chmod, symlink, readlink, utimes } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { dockerRecovery } from './docker-recovery.mjs';

const exec = promisify(execFile);
async function fixture(t) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'puppyone-recovery-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const root = path.join(directory, 'repo'), volume = path.join(directory, 'volume');
  await mkdir(root);
  const recovery = dockerRecovery(root, volume);
  await recovery.initialize();
  return { directory, root, volume, recovery };
}

test('restores actual Git metadata, dirty/index-only files, modes and symlinks', async t => {
  const { root, volume, recovery } = await fixture(t);
  const git = (...args) => exec('git', args, { cwd: root });
  await git('init', '--initial-branch=main');
  await git('config', 'user.name', 'Recovery test');
  await git('config', 'user.email', 'recovery@example.invalid');
  await writeFile(path.join(root, 'history.md'), 'published');
  await git('add', '.');
  await git('commit', '-m', 'base');
  const head = (await git('rev-parse', 'HEAD')).stdout;
  await mkdir(path.join(root, 'Archived'));
  await writeFile(path.join(root, 'Archived', '笔记.md'), 'untracked');
  await writeFile(path.join(root, 'run.sh'), '#!/bin/sh\nexit 0\n');
  await chmod(path.join(root, 'run.sh'), 0o755);
  await symlink('history.md', path.join(root, 'link'));
  await writeFile(path.join(root, 'history.md'), 'staged');
  await git('add', 'history.md');
  await writeFile(path.join(root, 'history.md'), 'unstaged');
  const saved = await recovery.snapshot();
  assert.equal((await lstat(volume)).mode & 0o777, 0o700);
  await rm(root, { recursive: true });
  await mkdir(root);
  await writeFile(path.join(root, 'discard-me.txt'), 'after checkpoint');
  await dockerRecovery(root, volume).restore(saved.id);
  assert.equal((await git('rev-parse', 'HEAD')).stdout, head);
  assert.equal((await git('show', ':history.md')).stdout, 'staged');
  assert.equal(await readFile(path.join(root, 'history.md'), 'utf8'), 'unstaged');
  assert.equal(await readFile(path.join(root, 'Archived', '笔记.md'), 'utf8'), 'untracked');
  assert.equal((await lstat(path.join(root, 'run.sh'))).mode & 0o777, 0o755);
  assert.equal(await readlink(path.join(root, 'link')), 'history.md');
  assert.equal((await readdir(root)).includes('discard-me.txt'), false);
  await git('fsck', '--full');
  const dirty = path.join(root, 'history.md'), stamp = await lstat(dirty);
  await writeFile(dirty, 'CHANGED!');
  await utimes(dirty, stamp.atime, stamp.mtime);
  await recovery.restore(saved.id);
  assert.equal(await readFile(dirty, 'utf8'), 'unstaged');
});

test('snapshots share unchanged bytes without mutating an earlier checkpoint', async t => {
  const { root, volume, recovery } = await fixture(t);
  await writeFile(path.join(root, 'stable'), 'unchanged');
  await writeFile(path.join(root, 'changed'), 'before');
  const stamp = await lstat(path.join(root, 'changed'));
  const first = await recovery.snapshot();
  await writeFile(path.join(root, 'changed'), 'AFTER!');
  await utimes(path.join(root, 'changed'), stamp.atime, stamp.mtime);
  const second = await recovery.snapshot();
  const before = path.join(volume, first.id), after = path.join(volume, second.id);
  assert.equal((await lstat(path.join(before, 'stable'))).ino, (await lstat(path.join(after, 'stable'))).ino);
  assert.notEqual((await lstat(path.join(root, 'stable'))).ino, (await lstat(path.join(after, 'stable'))).ino);
  assert.equal(await readFile(path.join(before, 'changed'), 'utf8'), 'before');
  assert.equal(await readFile(path.join(after, 'changed'), 'utf8'), 'AFTER!');
});

test('scratch files and runtime data outside the repository are not copied or restored', async t => {
  const { directory, root, volume, recovery } = await fixture(t);
  await writeFile(path.join(directory, 'scratch'), 'temporary');
  await writeFile(path.join(root, 'document.md'), 'project');
  const saved = await recovery.snapshot();
  assert.deepEqual(await readdir(path.join(volume, saved.id)), ['document.md']);
  await writeFile(path.join(directory, 'scratch'), 'new temporary state');
  await recovery.restore(saved.id);
  assert.equal(await readFile(path.join(directory, 'scratch'), 'utf8'), 'new temporary state');
});

test('rejects missing, traversing and symlink recovery locations before replacing files', async t => {
  const { directory, root, volume, recovery } = await fixture(t);
  await writeFile(path.join(root, 'keep'), 'safe');
  await assert.rejects(recovery.restore('../repo'), /Invalid recovery identity/);
  await assert.rejects(recovery.restore('00000000-0000-0000-0000-000000000000'), /ENOENT/);
  const id = '11111111-1111-1111-1111-111111111111';
  await symlink(directory, path.join(volume, id));
  await assert.rejects(recovery.restore(id), /Invalid recovery directory/);
  assert.equal(await readFile(path.join(root, 'keep'), 'utf8'), 'safe');
});
