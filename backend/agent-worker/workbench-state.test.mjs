import assert from 'node:assert/strict';
import test from 'node:test';
import { TASK_UID_RANGES, validTaskUid } from './workbench-state.mjs';

test('a command identity is one the image has no group for', () => {
  for (const uid of [20000, 59999, 100000, 1234567, 2000000000]) assert.equal(validTaskUid(uid), true, uid);
  // Below, between and beyond the ranges: system, image (nogroup is 65534) or unusable.
  for (const uid of [0, 1000, 19999, 60000, 65534, 99999, 2000000001, 4294967295]) assert.equal(validTaskUid(uid), false, uid);
  for (const value of [20000.5, '20000', NaN, null]) assert.equal(validTaskUid(value), false, String(value));
});

test('the database, gateway and worker agree on the ranges', async () => {
  const { readFile } = await import('node:fs/promises');
  const python = await readFile(new URL('./workbench-signals.py', import.meta.url), 'utf8');
  assert.match(python, new RegExp(`TASK_UID_RANGES = \\(${TASK_UID_RANGES.map(([low, high]) => `\\(${low}, ${high}\\)`).join(', ')}\\)`));
});
