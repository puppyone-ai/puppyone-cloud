import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const layout = await readFile(
  fileURLToPath(new URL('../app/(main)/layout.tsx', import.meta.url)),
  'utf8',
);

assert.doesNotMatch(
  layout,
  /getEnvironmentLabel|environmentLabel/,
  'the workspace rail must not render a browser-dependent environment label',
);

console.log('Hydration-safe workspace rail test passed.');
