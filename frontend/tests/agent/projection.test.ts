import { expect, it } from 'vitest';
import { projectRunMessages, runStatus } from '@/features/agent/runtime/projection';
import { makeRun } from './fixtures';
it('uses stable run/request identities and retains published replies without a second message store', () => {
  const messages = projectRunMessages([makeRun({ state: 'succeeded', snapshot: { text: 'Saved' } })]);
  expect(messages.map(message => message.id)).toEqual(['run-1:user', 'run-1:assistant']);
  expect(messages[1].content).toBe('Saved'); expect(messages[1].isStreaming).toBe(false);
});
it('does not fabricate completed tools after stopping or failure', () => {
  const messages = projectRunMessages([makeRun({ state: 'stopped', tools: [
    { call_id: 'a', name: 'read', input: {}, state: 'completed' },
    { call_id: 'b', name: 'write', input: {}, state: 'executing' },
  ] })]);
  expect(messages[1].parts?.map(part => part.toolStatus)).toEqual(['completed', 'error']);
});
it('distinguishes publishing, conflict and unknown save outcomes from successful completion', () => {
  expect(runStatus(makeRun({ state: 'publishing' }))).toContain('Saving');
  expect(runStatus(makeRun({ state: 'conflict' }))).toContain('conflict');
  expect(runStatus(makeRun({ state: 'outcome_unknown' }))).toContain('confirmation');
  expect(runStatus(makeRun({ state: 'succeeded' }))).toBeNull();
});
it('shows a recoverable failure on the tool while the Agent continues and finishes normally', () => {
  const tools = [{ call_id: 'denied', name: 'write', input: { path: '/recovery/test' }, state: 'completed' as const,
    result: { content: [{ type: 'text', text: 'Permission denied' }], isError: true,
      details: { code: 'tool_permission_denied', recoverable: true } } }];
  for (const state of ['running', 'succeeded'] as const) {
    const run = makeRun({ state, tools });
    const messages = projectRunMessages([run]);
    expect(messages[1].parts?.[0]).toMatchObject({ toolStatus: 'error', toolOutput: 'Permission denied' });
    expect(messages[1].isStreaming).toBe(state === 'running');
    expect(runStatus(run)).toBeNull();
  }
});
it('explains a declined tool without showing a run failure', () => {
  const run = makeRun({ state: 'running', tools: [{ call_id: 'declined', name: 'bash', input: {}, state: 'rejected' }] });
  expect(projectRunMessages([run])[1].parts?.[0]?.toolOutput).toContain('declined');
  expect(runStatus(run)).toBeNull();
});
