import { test } from 'node:test';
import assert from 'node:assert/strict';
import { workspaceTools } from './tools.mjs';

test('read-only composition never constructs execution or mutation tools', () => {
  const read = { name: 'read' };
  const forbidden = () => { throw new Error('mutation capability requested'); };
  assert.deepEqual(workspaceTools({ readonly: true,
    filesystem: { readTools: () => [read], writeTools: forbidden },
    execution: { tools: forbidden } }), [read]);
});

test('execution can change independently of filesystem definitions', async () => {
  const calls = [];
  const read = { name: 'read', execute: async path => calls.push(['files', path]) };
  const write = { name: 'write' };
  const bash = { name: 'bash', execute: async command => calls.push(['remote-execution', command]) };
  const tools = workspaceTools({ readonly: false,
    filesystem: { readTools: () => [read], writeTools: () => [write] },
    execution: { tools: () => [bash] } });
  await tools.find(tool => tool.name === 'read').execute('file.txt');
  await tools.find(tool => tool.name === 'bash').execute('cat file.txt');
  assert.deepEqual(calls, [['files', 'file.txt'], ['remote-execution', 'cat file.txt']]);
  assert.deepEqual(tools, [read, write, bash]);
});
