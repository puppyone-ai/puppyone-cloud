/** Real stock Git: how a Workspace takes in another branch. No provider or relay. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { git } from './git.mjs';
import { mergeBranch } from './shared-git.mjs';

const target = 'refs/heads/workspaces/owner', message = 'Bring in changes', width = 40;

async function workspace() {
  const root = await mkdtemp(path.join(tmpdir(), 'workspace-merge-'));
  const run = args => git(root, args);
  await run(['init', '--initial-branch=workspaces/owner', '.']);
  await run(['config', 'user.name', 'Test']);
  await run(['config', 'user.email', 'test@example.invalid']);
  const commit = async (file, text, label) => {
    await writeFile(path.join(root, file), text);
    await run(['add', '-A']);
    await run(['commit', '-q', '-m', label]);
    return run(['rev-parse', 'HEAD']);
  };
  const base = await commit('note.md', 'one\ntwo\nthree\n', 'base');
  return { root, run, commit, base };
}

async function branchFrom({ run, commit }, start, file, text) {
  const current = await run(['rev-parse', 'HEAD']);
  await run(['checkout', '-q', '-b', 'incoming', start]);
  const source = await commit(file, text, 'incoming');
  await run(['checkout', '-q', 'workspaces/owner']);
  assert.equal(await run(['rev-parse', 'HEAD']), current);
  return source;
}

test('a branch the Workspace already contains changes nothing', async () => {
  const space = await workspace();
  try {
    const tip = await space.commit('local.md', 'local', 'local');
    assert.deepEqual(await mergeBranch(space.run, { tip, source: space.base, target, message, width }),
      { status: 'up_to_date', tip });
  } finally { await rm(space.root, { recursive: true, force: true }); }
});

test('a Workspace with no work of its own fast-forwards and shows the new files', async () => {
  const space = await workspace();
  try {
    const source = await branchFrom(space, space.base, 'agent.md', 'from the agent');
    const result = await mergeBranch(space.run, { tip: space.base, source, target, message, width });
    assert.deepEqual(result, { status: 'fast_forward', tip: source });
    assert.equal(await readFile(path.join(space.root, 'agent.md'), 'utf8'), 'from the agent');
    assert.equal(await space.run(['rev-parse', target]), source);
  } finally { await rm(space.root, { recursive: true, force: true }); }
});

test('separate work merges into a commit with both parents', async () => {
  const space = await workspace();
  try {
    const source = await branchFrom(space, space.base, 'agent.md', 'from the agent');
    const tip = await space.commit('local.md', 'local', 'local');
    const result = await mergeBranch(space.run, { tip, source, target, message, width });
    assert.equal(result.status, 'merged');
    assert.equal(await space.run(['rev-parse', `${result.tip}^1`]), tip);
    assert.equal(await space.run(['rev-parse', `${result.tip}^2`]), source);
    assert.equal(await space.run(['rev-parse', target]), result.tip);
    assert.equal(await readFile(path.join(space.root, 'agent.md'), 'utf8'), 'from the agent');
    assert.equal(await readFile(path.join(space.root, 'local.md'), 'utf8'), 'local');
    assert.equal(await space.run(['status', '--porcelain']), '');
  } finally { await rm(space.root, { recursive: true, force: true }); }
});

test('a real conflict leaves marked files and commits nothing', async () => {
  const space = await workspace();
  try {
    const source = await branchFrom(space, space.base, 'note.md', 'one\nagent\nthree\n');
    const tip = await space.commit('note.md', 'one\nhuman\nthree\n', 'local');
    const result = await mergeBranch(space.run, { tip, source, target, message, width });
    assert.deepEqual(result, { status: 'conflict', tip, source, paths: ['note.md'] });
    const text = await readFile(path.join(space.root, 'note.md'), 'utf8');
    assert.match(text, /^<{7} /m);
    assert.match(text, /human/);
    assert.match(text, /agent/);
    assert.equal(await space.run(['rev-parse', target]), tip);
  } finally { await rm(space.root, { recursive: true, force: true }); }
});

test('a conflict alongside clean changes still brings the clean file in', async () => {
  const space = await workspace();
  try {
    await space.run(['checkout', '-q', '-b', 'incoming', space.base]);
    await writeFile(path.join(space.root, 'note.md'), 'one\nagent\nthree\n');
    await writeFile(path.join(space.root, 'agent.md'), 'from the agent');
    await space.run(['add', '-A']);
    await space.run(['commit', '-q', '-m', 'incoming']);
    const source = await space.run(['rev-parse', 'HEAD']);
    await space.run(['checkout', '-q', 'workspaces/owner']);
    const tip = await space.commit('note.md', 'one\nhuman\nthree\n', 'local');
    const result = await mergeBranch(space.run, { tip, source, target, message, width });
    assert.deepEqual(result.paths, ['note.md']);
    assert.equal(await readFile(path.join(space.root, 'agent.md'), 'utf8'), 'from the agent');
  } finally { await rm(space.root, { recursive: true, force: true }); }
});
