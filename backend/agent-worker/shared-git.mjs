/** Shared native Git operations without reading or rewriting user Git config. */
import { mkdir, lstat, readFile } from 'node:fs/promises';
import { ROOT, git, exists } from './git.mjs';
import { gitControl } from './workbench-git-layout.mjs';

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
  if (action === 'finalize') {
    if (config.readonly) return { ...before, changed: false, automatic_commit: false };
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
    if (candidate !== config.base_oid && config.base_oid)
      await run(['merge-base', '--is-ancestor', config.base_oid, candidate]);
    await inspect(); // Detect a branch switch, but never replace our captured OID with a newer HEAD.
    return { ...before, tip: candidate, changed: candidate !== config.base_oid,
      automatic_commit: candidate !== before.tip };
  }
  if (action === 'push') {
    const candidate = value.candidate;
    if (config.readonly || typeof candidate !== 'string' || !new RegExp(`^[0-9a-f]{${width}}$`).test(candidate))
      throw new Error('Invalid publication candidate');
    if (await run(['cat-file', '-t', candidate]) !== 'commit') throw new Error('Publication candidate is not a commit');
    try { return { output: await run(['push', '--porcelain', '--no-verify', remote, `${candidate}:${target}`]) }; }
    catch (error) {
      if (/^!\t[^\n]+\t\[rejected\] \((fetch first|non-fast-forward)\)$/m.test(error.output || '')) return { rejected: true };
      throw error;
    }
  }
  throw new Error('Unknown workbench Git operation');
}
