import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const helperPath = new URL('../lib/backendProxyHeaders.ts', import.meta.url);
const source = await readFile(helperPath, 'utf8');
const output = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
});
const moduleUrl = `data:text/javascript;base64,${Buffer.from(output.outputText).toString('base64')}`;
const { forwardBackendRequestHeaders } = await import(moduleUrl);

const forwarded = forwardBackendRequestHeaders(new Headers({
  Authorization: 'Bearer access-token',
  'Idempotency-Key': '1aa4f40c-b10f-4ddd-98bb-d4762b14fe09',
  'X-PuppyOne-Repository-Contract': '2',
  'X-Do-Not-Forward': 'private',
}));

assert.equal(forwarded.get('authorization'), 'Bearer access-token');
assert.equal(forwarded.get('idempotency-key'), '1aa4f40c-b10f-4ddd-98bb-d4762b14fe09');
assert.equal(forwarded.get('x-puppyone-repository-contract'), '2');
assert.equal(forwarded.get('x-do-not-forward'), null);

console.log('BFF repository contract forwarding test passed.');
