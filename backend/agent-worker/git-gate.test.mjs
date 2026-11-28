import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync, chmodSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

const REAL = execFileSync('/bin/sh', ['-c', 'command -v git'], { encoding: 'utf8' }).trim();
const IDENTITY = { GIT_AUTHOR_NAME: 'a', GIT_AUTHOR_EMAIL: 'a@a', GIT_COMMITTER_NAME: 'a', GIT_COMMITTER_EMAIL: 'a@a' };

function workspace() {
  const base = realpathSync(mkdtempSync(path.join(tmpdir(), 'git-gate-')));
  const root = path.join(base, 'repo');
  const run = (...args) => execFileSync(REAL, args, { cwd: root, encoding: 'utf8', env: { ...process.env, ...IDENTITY } }).trim();
  execFileSync(REAL, ['init', '-q', '-b', 'agents/a1', root]);
  writeFileSync(path.join(root, 'a.txt'), 'one\n');
  run('add', '-A'); run('commit', '-qm', 'c1');
  run('branch', 'other');
  writeFileSync(path.join(root, 'a.txt'), 'two\n');
  run('add', '-A'); run('commit', '-qm', 'c2');
  const gate = path.join(base, 'git');
  // Only the two path constants change; there is deliberately no environment switch.
  writeFileSync(gate, readFileSync(new URL('./git-gate.sh', import.meta.url), 'utf8')
    .replace('REAL=/usr/bin/git', `REAL=${REAL}`).replace('ROOT=/workspace/repo', `ROOT=${root}`));
  chmodSync(gate, 0o755);
  const git = (args, cwd = root) => spawnSync(gate, args, { cwd, encoding: 'utf8', env: { ...process.env, ...IDENTITY } });
  return { base, root, run, git, done: () => rmSync(base, { recursive: true, force: true }) };
}

const unchanged = ({ root, run }, tip) => {
  assert.equal(readFileSync(path.join(root, 'a.txt'), 'utf8'), 'two\n');
  assert.equal(run('symbolic-ref', 'HEAD'), 'refs/heads/agents/a1');
  assert.equal(run('rev-parse', 'HEAD'), tip);
  assert.equal(run('status', '--porcelain'), '');
};

for (const args of [
  ['switch', 'other'], ['switch', '-c', 'feature'], ['switch', '--detach'],
  ['checkout', 'other'], ['checkout', '-b', 'feature'], ['checkout', '-B', 'feature'],
  ['checkout', '--orphan', 'feature'], ['checkout', '--detach'], ['checkout', 'HEAD~1'],
  ['checkout', '-f', 'other'], ['checkout', '-q', 'other'], ['checkout', '-'],
  ['-C', '.', 'switch', 'other'], ['-c', 'x.y=z', 'switch', 'other'],
  ['-c', 'alias.s=switch', 's', 'other'],
  ['rebase', 'other'], ['bisect', 'start'], ['update-ref', 'refs/heads/agents/a1', 'other'],
  ['symbolic-ref', 'HEAD', 'refs/heads/other'], ['symbolic-ref', '--delete', 'HEAD'],
  ['worktree', 'add', '../wt', 'other'], ['stash', 'branch', 'feature'],
  ['branch', '-m', 'renamed'], ['branch', '--move', 'agents/a1', 'renamed'],
]) {
  test(`refused before touching anything: git ${args.join(' ')}`, () => {
    const case_ = workspace();
    try {
      const tip = case_.run('rev-parse', 'HEAD');
      const result = case_.git(args);
      assert.equal(result.status, 1, result.stderr);
      assert.match(result.stderr, /stays on one branch/);
      assert.match(result.stderr, /Commit on the current branch/);
      unchanged(case_, tip);
    } finally { case_.done(); }
  });
}

test('an alias in the repository config cannot hide a switch', () => {
  const case_ = workspace();
  try {
    case_.run('config', 'alias.go', 'switch');
    const tip = case_.run('rev-parse', 'HEAD');
    assert.equal(case_.git(['go', 'other']).status, 1);
    unchanged(case_, tip);
  } finally { case_.done(); }
});

for (const [name, args] of Object.entries({
  'restore with --': ['checkout', '--', 'a.txt'],
  'restore from a commit': ['checkout', 'HEAD~1', '--', 'a.txt'],
  'restore from a commit without --': ['checkout', 'HEAD~1', 'a.txt'],
  'restore a path': ['checkout', 'a.txt'],
  'restore everything': ['checkout', '.'],
  'the branch it is on': ['checkout', 'agents/a1'],
  HEAD: ['checkout', 'HEAD'],
  status: ['status', '-s'],
  log: ['log', '--oneline', '-1'],
  'a new branch without moving': ['branch', 'feature'],
  'list branches': ['branch', '--list'],
  'read the current branch': ['symbolic-ref', '--short', 'HEAD'],
  'list worktrees': ['worktree', 'list'],
  'stash list': ['stash', 'list'],
  version: ['--version'],
})) {
  test(`allowed: ${name}`, () => {
    const case_ = workspace();
    try {
      const result = case_.git(args);
      assert.equal(result.status, 0, result.stderr);
      assert.doesNotMatch(result.stderr, /stays on one branch/);
    } finally { case_.done(); }
  });
}

test('ordinary work still commits, amends and merges', () => {
  const case_ = workspace();
  try {
    writeFileSync(path.join(case_.root, 'b.txt'), 'x\n');
    assert.equal(case_.git(['add', '-A']).status, 0);
    assert.equal(case_.git(['commit', '-qm', 'n']).status, 0);
    assert.equal(case_.git(['commit', '-q', '--amend', '-m', 're']).status, 0);
    assert.equal(case_.git(['merge', '-q', '--no-edit', '-X', 'ours', 'other']).status, 0);
    assert.equal(case_.git(['reset', '-q', '--hard', 'HEAD~1']).status, 0);
  } finally { case_.done(); }
});

test('another repository on the machine is not this Workspace', () => {
  const case_ = workspace();
  try {
    const other = path.join(case_.base, 'elsewhere');
    execFileSync(REAL, ['init', '-q', '-b', 'main', other]);
    const result = case_.git(['switch', '-c', 'feature'], other);
    assert.equal(result.status, 0, result.stderr);
  } finally { case_.done(); }
});
