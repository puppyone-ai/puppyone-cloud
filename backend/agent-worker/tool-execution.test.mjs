import { test } from 'node:test';
import assert from 'node:assert/strict';
import { wrapTool, approvalDenied } from './tool-execution.mjs';

test('permission errors are returned and checkpointed, then another tool can succeed', async () => {
  const frames = [];
  const context = { conversation: () => ({ entries: [] }), isStopped: () => false,
    request: async (type, frame) => { frames.push({ type, ...frame }); return { allow: true }; } };
  const denied = wrapTool({ name: 'write', execute: async () => { throw Object.assign(new Error('Permission denied'), { code: 'EACCES' }); } }, context);
  const result = await denied.execute('bad-write', { path: '/recovery/test' });
  assert.equal(result.isError, true);
  assert.equal(result.details.code, 'tool_permission_denied');
  assert.equal(frames[1].type, 'tool_end');
  assert.equal(frames[1].mutated, true); // Failed writes may have partial effects.
  const good = wrapTool({ name: 'read', execute: async () => ({ content: [{ type: 'text', text: 'OK' }] }) }, context);
  assert.equal((await good.execute('next-read', { path: '/etc/os-release' })).content[0].text, 'OK');
});

test('durable admission results and declined approvals never execute the command', async () => {
  for (const admission of [{ result: approvalDenied() }, { allow: false }]) {
    let executed = false;
    const types = [];
    const tool = wrapTool({ name: 'bash', execute: async () => { executed = true; } }, {
      conversation: () => ({}), isStopped: () => false,
      request: async type => { types.push(type); return admission; },
    });
    assert.deepEqual(await tool.execute('declined', { command: 'touch nope' }), approvalDenied());
    assert.equal(executed, false);
    assert.deepEqual(types, ['tool_start']);
  }
});

test('control-channel failures and stop remain fatal instead of becoming tool results', async () => {
  for (const failureAt of ['tool_start', 'tool_end']) {
    const tool = wrapTool({ name: 'bash', execute: async () => ({ content: [] }) }, {
      conversation: () => ({}), isStopped: () => false,
      request: async type => { if (type === failureAt) throw new Error('Control channel lost'); return { allow: true }; },
    });
    await assert.rejects(tool.execute('call', { command: 'true' }), /Control channel lost/);
  }
  const stopped = wrapTool({ name: 'read' }, { isStopped: () => true });
  await assert.rejects(stopped.execute('call', {}), /Run stopped/);
});
