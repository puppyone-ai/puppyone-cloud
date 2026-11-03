/** Shared native Git operations without reading or rewriting user Git config. */
import { mkdir, lstat, readFile, writeFile, rm } from 'node:fs/promises';
import { ROOT, git, exists } from './git.mjs';
import { gitControl } from './workbench-git-layout.mjs';

const MERGE_STATE = `${ROOT}/.git/puppyone-merge.json`;

async function isAncestor(run, older, newer) {
  try { await run(['merge-base', '--is-ancestor', older, newer]); return true; }
  catch (error) { if (error.code === 1) return false; throw error; }
}

/**
 * Bring source into tip through plumbing: no hooks, no user config, and HEAD stays
 * on the Workspace branch. A conflict leaves marked files in the worktree, so they
 * are resolved where the work happens, and nothing is committed until then.
 */
export async function mergeBranch(run, { tip, source, target, message, width }) {
  if (tip === source || (tip && await isAncestor(run, source, tip))) return { status: 'up_to_date', tip };
  const advance = async next => {
    await run(tip ? ['read-tree', '-m', '-u', tip, next] : ['read-tree', '-m', '-u', next]);
    await run(['update-ref', '--create-reflog', '-m', message, target, next, tip || '0'.repeat(width)]);
  };
  if (!tip || await isAncestor(run, tip, source)) {
    await advance(source);
    return { status: 'fast_forward', tip: source };
  }
  let tree, paths = [];
  try { tree = (await run(['merge-tree', '--write-tree', '--name-only', '--no-messages', tip, source])).split('\n')[0]; }
  catch (error) {
    if (error.code !== 1 || !error.output) throw error;
    const lines = error.output.split('\n').filter(Boolean);
    tree = lines[0]; paths = lines.slice(1);
  }
  if (paths.length) {
    await run(['read-tree', '-m', '-u', tip, tree]);
    return { status: 'conflict', tip, source, paths };
  }
  const merged = await run(['commit-tree', tree, '-p', tip, '-p', source, '-m', message]);
  await advance(merged);
  return { status: 'merged', tip: merged };
}

export async function sharedGit(action, config, value, remote, transport = {}) {
  const target = config.target_ref, directory = gitControl(config.object_format);
  const width = config.object_format === 'sha256' ? 64 : 40;
  if (typeof target !== 'string' || !target.startsWith('refs/heads/')) throw new Error('Invalid workspace target');
  const run = args => git(ROOT, args, { ...transport, GIT_DIR: directory, GIT_WORK_TREE: ROOT,
    GIT_INDEX_FILE: `${ROOT}/.git/index`, GIT_ATTR_NOSYSTEM: '1' });
  await run(['check-ref-format', target]);

  async function inspect() {
    const dot = await lstat(`${ROOT}/.git`);
    if (!dot.isDirectory() || dot.isSymbolicLink()) throw new Error('Invalid Git directory');
    if ((await readFile(`${ROOT}/.git/HEAD`, 'utf8')).trimEnd() !== `ref: ${target}`)
      throw new Error('Agent changed its selected branch');
    let tip = null;
    try { tip = await run(['rev-parse', '--verify', target]); }
    catch (error) { if (error.code !== 128) throw error; }
    return { object_format: config.object_format, target_ref: target, tip };
  }

  if (action === 'prepare') {
    if (!(await exists(`${ROOT}/.git`)))
      await git(ROOT, ['init', `--object-format=${config.object_format}`, '--initial-branch', target.slice(11), '.']);
    await mkdir(`${ROOT}/.git/logs`, { recursive: true });
    const before = await inspect();
    if (before.tip || await run(['ls-files', '--cached', '--others']))
      throw new Error('Cannot initialize over existing shared work');
    if (config.base_oid) {
      await run(['fetch', '--no-tags', '--no-write-fetch-head', remote, `+${config.source_ref || target}:refs/remotes/origin/selected`]);
      const fetched = await run(['rev-parse', 'refs/remotes/origin/selected']);
      if (fetched !== config.base_oid) throw new Error('Cloud branch changed while preparing workspace');
      await run(['update-ref', target, fetched, '0'.repeat(width)]);
      await run(['read-tree', '--reset', '-u', fetched]);
    }
    return inspect();
  }
  const before = await inspect();
  if (action === 'inspect') return before;
  if (action === 'verify-initialization') {
    if (before.tip !== (config.base_oid ?? null)) throw new Error('Initialization candidate mismatch');
    if (before.tip) {
      await run(['diff-index', '--cached', '--quiet', before.tip, '--']);
      await run(['diff-files', '--quiet', '--']);
    } else if (await run(['ls-files', '--cached'])) throw new Error('Initialization has staged files');
    if (await run(['ls-files', '--others'])) throw new Error('Initialization has unique files');
    return before;
  }
  // A waiting merge owns the worktree: its marked files must not become a commit.
  const merging = async () => (await exists(MERGE_STATE)) ? JSON.parse(await readFile(MERGE_STATE, 'utf8')) : null;
  async function save(message) {
    await run(['add', '-A', '--', '.']);
    const previousTree = before.tip || await run(['hash-object', '-w', '-t', 'tree', '/dev/null']);
    try { await run(['diff', '--cached', '--quiet', '--exit-code', previousTree, '--']); return before.tip; }
    catch (error) { if (error.code !== 1) throw error; }
    const tree = await run(['write-tree']);
    const candidate = await run(['commit-tree', tree, ...(before.tip ? ['-p', before.tip] : []), '-m', message]);
    await run(['update-ref', '--create-reflog', '-m', message, target, candidate, before.tip || '0'.repeat(width)]);
    return candidate;
  }
  if (action === 'finalize') {
    if (config.readonly || await merging()) return { ...before, changed: false, automatic_commit: false };
    await run(['add', '-A', '--', '.']);
    let candidate = before.tip;
    const previousTree = before.tip || await run(['hash-object', '-w', '-t', 'tree', '/dev/null']);
    try { await run(['diff', '--cached', '--quiet', '--exit-code', previousTree, '--']); }
    catch (error) {
      if (error.code !== 1) throw error;
      const tree = await run(['write-tree']);
      candidate = await run(['commit-tree', tree, ...(before.tip ? ['-p', before.tip] : []), '-m', value.message]);
      // Native local CAS, not a copied HEAD/ref database. No hooks, editor or
      // user config runs with the relay capability. The shared index stays shared.
      await run(['update-ref', '--create-reflog', '-m', value.message, target, candidate, before.tip || '0'.repeat(width)]);
    }
    // A retained workspace need not contain the current remote base: incoming
    // changes are merged explicitly. Preserve the local candidate first; push
    // and the host's ancestry/CAS checks decide whether it can be published.
    await inspect(); // Detect a branch switch, but never replace our captured OID with a newer HEAD.
    return { ...before, tip: candidate, changed: Boolean(candidate && candidate !== config.base_oid),
      automatic_commit: candidate !== before.tip };
  }
  if (action === 'push') {
    const candidate = value.candidate;
    if (config.readonly || typeof candidate !== 'string' || !new RegExp(`^[0-9a-f]{${width}}$`).test(candidate))
      throw new Error('Invalid publication candidate');
    if (await run(['cat-file', '-t', candidate]) !== 'commit') throw new Error('Publication candidate is not a commit');
    // A parallel task may already have published this work inside a later commit.
    if (value.remote_tip !== undefined) {
      if (typeof value.remote_tip !== 'string' || !new RegExp(`^[0-9a-f]{${width}}$`).test(value.remote_tip))
        throw new Error('Invalid publication remote tip');
      try { await run(['merge-base', '--is-ancestor', candidate, value.remote_tip]); return { contained: true }; }
      catch { /* Not contained, or the tip is unknown here: let the push decide. */ }
    }
    try { return { output: await run(['push', '--porcelain', '--no-verify', remote, `${candidate}:${target}`]) }; }
    catch (error) {
      if (/^!\t[^\n]+\t\[rejected\] \((fetch first|non-fast-forward)\)$/m.test(error.output || '')) return { rejected: true };
      throw error;
    }
  }
  if (action === 'save') {
    if (await merging()) return { ...before, status: 'merging' };
    const tip = await save(value.message);
    return { ...before, tip, status: 'saved', changed: tip !== before.tip };
  }
  if (action === 'sync') {
    const waiting = await merging();
    if (waiting) return { status: 'conflict', tip: before.tip, ...waiting };
    if (typeof value.source_ref !== 'string' || !value.source_ref.startsWith('refs/heads/'))
      throw new Error('Invalid sync source');
    await run(['check-ref-format', value.source_ref]);
    const tip = await save(value.message);
    await run(['fetch', '--no-tags', '--no-write-fetch-head', remote, `+${value.source_ref}:refs/remotes/origin/sync`]);
    const source = await run(['rev-parse', 'refs/remotes/origin/sync']);
    const result = await mergeBranch(run, { tip, source, target, message: value.message, width });
    if (result.status === 'conflict')
      await writeFile(MERGE_STATE, JSON.stringify({ source, source_ref: value.source_ref, paths: result.paths }));
    return result;
  }
  if (action === 'sync-finish' || action === 'sync-abort') {
    const waiting = await merging();
    if (!waiting) return { status: 'up_to_date', tip: before.tip };
    if (action === 'sync-abort') {
      await run(['read-tree', '--reset', '-u', before.tip]);
      await rm(MERGE_STATE);
      return { status: 'aborted', tip: before.tip };
    }
    const unresolved = [];
    for (const path of waiting.paths) {
      const text = await readFile(`${ROOT}/${path}`, 'utf8').catch(() => '');
      if (/^(<{7}|>{7})( |$)/m.test(text)) unresolved.push(path);
    }
    if (unresolved.length) return { status: 'conflict', tip: before.tip, ...waiting, paths: unresolved };
    await run(['add', '-A', '--', '.']);
    const tree = await run(['write-tree']);
    const merged = await run(['commit-tree', tree, '-p', before.tip, '-p', waiting.source, '-m', value.message]);
    await run(['update-ref', '--create-reflog', '-m', value.message, target, merged, before.tip]);
    await rm(MERGE_STATE);
    return { status: 'merged', tip: merged };
  }
  if (action === 'publish') {
    if (await merging()) return { status: 'merging', tip: before.tip };
    const destination = value.destination_ref;
    if (typeof destination !== 'string' || !destination.startsWith('refs/heads/'))
      throw new Error('Invalid publication destination');
    await run(['check-ref-format', destination]);
    if (!before.tip) return { status: 'no_changes', tip: null };
    try { return { status: 'pushed', tip: before.tip, output: await run(['push', '--porcelain', '--no-verify', remote, `${before.tip}:${destination}`]) }; }
    catch (error) {
      if (/^!\t[^\n]+\t\[rejected\] \((fetch first|non-fast-forward)\)$/m.test(error.output || '')) return { status: 'rejected', tip: before.tip };
      throw error;
    }
  }
  throw new Error('Unknown workbench Git operation');
}
