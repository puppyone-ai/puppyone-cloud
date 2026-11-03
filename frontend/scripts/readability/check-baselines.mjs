import { readFileSync } from 'node:fs';
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { assertShrinks, dependencyCounts, suppressionCounts } from './baselines.mjs';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const base = process.argv[2] ?? process.env.READABILITY_BASE_REF;
if (!base) throw new Error('Supply the PR base / push-before commit as READABILITY_BASE_REF or an argument.');
execFileSync('git', ['rev-parse', '--verify', `${base}^{commit}`], { cwd: root, stdio: 'pipe' });

function previousBaseline(path) {
  const result = spawnSync('git', ['show', `${base}:${path}`], { cwd: root, encoding: 'utf8' });
  if (result.status !== 0) throw new Error(`Base ${base} has no ${path}: ${result.stderr.trim()}`);
  return JSON.parse(result.stdout);
}

function check(path, count) {
  const previous = count(previousBaseline(path));
  const current = count(JSON.parse(readFileSync(`${root}/${path}`, 'utf8')));
  assertShrinks(previous, current, path);
  const total = [...current.values()].reduce((sum, value) => sum + value, 0);
  console.log(`${path}: ${current.size} entries, ${total} violations; no baseline increase.`);
}

check('frontend/eslint-suppressions.json', suppressionCounts);
check('frontend/.dependency-cruiser-known-violations.json', dependencyCounts);
