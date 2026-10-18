/** Real Pi harness/control protocol with a deterministic model; no paid inference. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { mkdtemp, mkdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { approvalDenied } from './tool-execution.mjs';
import { createPiLoop } from './loop.mjs';
import { localFilesystem } from './filesystem.mjs';
import { localExecution } from './execution.mjs';

async function runHarness(steps, { declined = false, readonly = false } = {}) {
  const temporary = await mkdtemp(path.join(tmpdir(), 'puppyone-pi-tools-'));
  const cwd = path.join(temporary, 'repo');
  await mkdir(cwd);
  const worker = spawn(process.execPath, [fileURLToPath(new URL('./worker.mjs', import.meta.url))], { cwd, stdio: ['pipe', 'pipe', 'pipe'] });
  let stderr = '', calls = 0;
  const receipts = [], checkpoints = [], outputs = [], observed = [];
  worker.stderr.on('data', bytes => { stderr += bytes.toString(); });
  const send = value => worker.stdin.write(`${JSON.stringify(value)}\n`);
  const lines = createInterface({ input: worker.stdout });
  const finished = new Promise((resolve, reject) => {
    worker.on('exit', (code, signal) => { reject(new Error(`Worker exited ${code}/${signal}; frames=${observed.join(',')}; ${stderr.slice(-2000)}`)); });
    (async () => {
      for await (const line of lines) {
        const frame = JSON.parse(line);
        observed.push(frame.type);
        if (frame.type === 'failed') throw new Error(frame.error);
        if (frame.type === 'finished') { assert.equal(frame.error, null); resolve(); break; }
        if (frame.type === 'model_request') {
          if (calls) {
            const tools = frame.request.messages.filter(message => message.role === 'tool');
            const last = JSON.stringify(tools.at(-1));
            assert.match(last, steps[calls - 1].expected);
            outputs.push(last);
          }
          const step = steps[calls++];
          const delta = step ? { role: 'assistant', tool_calls: [{ index: 0, id: `call-${calls}`, type: 'function',
            function: { name: step.name, arguments: JSON.stringify(step.input(temporary)) } }] }
            : { role: 'assistant', content: 'Recovered and completed.' };
          const chunk = { id: `model-${calls}`, object: 'chat.completion.chunk', created: 1, model: 'fixture',
            choices: [{ index: 0, delta, finish_reason: null }] };
          send({ type: 'model_chunk', id: frame.id, frame: chunk });
          send({ type: 'model_chunk', id: frame.id, frame: { ...chunk,
            choices: [{ index: 0, delta: {}, finish_reason: step ? 'tool_calls' : 'stop' }],
            usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } } });
          send({ type: 'model_end', id: frame.id, error: null });
        } else if (frame.type === 'tool_start') {
          receipts.push(frame);
          const sdkResult = readonly && frame.name === 'write'
            ? { content: [{ type: 'text', text: 'This run is read-only. Continue using read tools.' }],
              isError: true, details: { code: 'tool_permission_denied', recoverable: true } }
            : frame.error_result;
          send({ type: 'reply', id: frame.id, ...(frame.error_result ? { result: sdkResult }
            : declined && receipts.length === 1 ? { result: approvalDenied() } : { allow: true }) });
        } else if (frame.type === 'tool_end') {
          receipts.push(frame);
          send({ type: 'reply', id: frame.id });
        } else if (frame.type === 'checkpoint') {
          checkpoints.push(frame); send({ type: 'reply', id: frame.id });
        }
      }
    })().catch(reject);
  });
  const timeout = setTimeout(() => worker.kill('SIGKILL'), 20000);
  try {
    send({ type: 'start', config: { model: 'fixture', context_window: 8192, max_tokens: 1024,
      prompt: 'Exercise tools and recover.', readonly, entries: undefined } });
    await finished;
    assert.equal(calls, steps.length + 1);
    assert.equal(checkpoints.at(-1)?.reason, 'settled');
    return { receipts, outputs, files: await readFile(path.join(cwd, 'result.txt'), 'utf8').catch(() => null) };
  } finally {
    clearTimeout(timeout); worker.kill('SIGKILL'); lines.close();
    await rm(temporary, { recursive: true, force: true });
  }
}

test('real Pi accepts independently hosted execution and filesystem tools', { timeout: 25000 }, async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'puppyone-pi-capabilities-'));
  const calls = [], files = new Map();
  // Retain the real SDK schemas, replace where the operations execute.
  const bash = { ...localExecution(root).tools()[0], execute: async (_id, input) => {
    calls.push(['execution', input.command]);
    files.set('result.txt', 'external-content');
    return { content: [{ type: 'text', text: 'remote command completed' }] };
  } };
  const read = { ...localFilesystem(root).readTools()[0], execute: async (_id, input) => {
    calls.push(['filesystem', input.path]);
    return { content: [{ type: 'text', text: files.get(input.path) }] };
  } };
  let modelCalls = 0, resolve, reject;
  const finished = new Promise((yes, no) => { resolve = yes; reject = no; });
  const loop = createPiLoop({ root, agentDir: path.join(root, 'agent'),
    filesystem: { readTools: () => [read], writeTools: () => [] },
    execution: { tools: () => [bash] },
    send: frame => queueMicrotask(() => {
      try {
        if (frame.type === 'failed') throw new Error(frame.error);
        if (frame.type === 'finished') { assert.equal(frame.error, null); resolve(); }
        if (frame.type === 'checkpoint' || frame.type === 'tool_end')
          loop.receive({ type: 'reply', id: frame.id });
        if (frame.type === 'tool_start') loop.receive({ type: 'reply', id: frame.id, allow: true });
        if (frame.type !== 'model_request') return;
        const index = modelCalls++;
        if (index === 2) assert.match(JSON.stringify(frame.request.messages.at(-1)), /external-content/);
        const tool = [ ['bash', { command: 'write using remote execution' }], ['read', { path: 'result.txt' }] ][index];
        const delta = tool ? { role: 'assistant', tool_calls: [{ index: 0, id: `call-${index}`,
          type: 'function', function: { name: tool[0], arguments: JSON.stringify(tool[1]) } }] }
          : { role: 'assistant', content: 'Done' };
        const chunk = { id: `model-${index}`, object: 'chat.completion.chunk', created: 1, model: 'fixture',
          choices: [{ index: 0, delta, finish_reason: null }] };
        loop.receive({ type: 'model_chunk', id: frame.id, frame: chunk });
        loop.receive({ type: 'model_chunk', id: frame.id, frame: { ...chunk,
          choices: [{ index: 0, delta: {}, finish_reason: tool ? 'tool_calls' : 'stop' }],
          usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } } });
        loop.receive({ type: 'model_end', id: frame.id });
      } catch (error) { reject(error); }
    }),
  });
  try {
    loop.receive({ type: 'start', config: { model: 'fixture', context_window: 8192,
      max_tokens: 1024, prompt: 'Exercise supplied tools', readonly: false } });
    await finished;
    assert.equal(modelCalls, 3);
    assert.deepEqual(calls, [['execution', 'write using remote execution'], ['filesystem', 'result.txt']]);
    await assert.rejects(readFile(path.join(root, 'result.txt')), { code: 'ENOENT' });
  } finally { loop.close(); await rm(root, { recursive: true, force: true }); }
});

test('Pi continues after a failing Bash command and can write the next file', { timeout: 25000 }, async () => {
  const result = await runHarness([
    { name: 'bash', input: () => ({ command: 'printf partial > partial.txt; exit 7' }), expected: /7/ },
    { name: 'write', input: () => ({ path: 'result.txt', content: 'recovered' }), expected: /result.txt/ },
  ]);
  assert.equal(result.files, 'recovered');
  assert.equal(result.receipts[1].result.isError, true);
  assert.equal(result.receipts[1].mutated, true);
});

test('Pi can use absolute scratch paths outside the project and /dev/null', { timeout: 25000 }, async () => {
  const result = await runHarness([
    { name: 'write', input: root => ({ path: path.join(root, 'scratch.txt'), content: 'scratch' }), expected: /scratch.txt/ },
    { name: 'read', input: root => ({ path: path.join(root, 'scratch.txt') }), expected: /scratch/ },
    { name: 'bash', input: () => ({ command: 'ls . >/dev/null; printf NORMAL_COMMAND_OK' }), expected: /NORMAL_COMMAND_OK/ },
  ]);
  assert.equal(result.files, null);
});

test('Pi receives a declined tool result and continues without executing it', { timeout: 25000 }, async () => {
  const result = await runHarness([
    { name: 'write', input: () => ({ path: 'result.txt', content: 'never' }), expected: /declined/ },
    { name: 'ls', input: () => ({ path: '.' }), expected: /empty|No files/i },
  ], { declined: true });
  assert.equal(result.files, null);
  assert.equal(result.receipts.filter(frame => frame.type === 'tool_end').length, 1);
});

test('Pi continues when a model requests a tool that is unavailable in read-only mode', { timeout: 25000 }, async () => {
  const result = await runHarness([
    { name: 'write', input: () => ({ path: 'result.txt', content: 'never' }), expected: /read-only/i },
    { name: 'ls', input: () => ({ path: '.' }), expected: /empty|No files/i },
  ], { readonly: true });
  assert.equal(result.files, null);
  assert.equal(result.receipts[0].name, 'write');
  assert.equal(result.receipts[0].error_result.isError, true);
  assert.equal(result.receipts[1].name, 'ls');
});

test('SDK argument errors produce a tool receipt before Pi continues', { timeout: 25000 }, async () => {
  const result = await runHarness([
    { name: 'write', input: () => ({ path: 'result.txt' }), expected: /content|required|validat/i },
    { name: 'write', input: () => ({ path: 'result.txt', content: 'corrected' }), expected: /result.txt/ },
  ]);
  assert.equal(result.files, 'corrected');
  assert.equal(result.receipts[0].error_result.isError, true);
  assert.equal(result.receipts.filter(frame => frame.type === 'tool_end').length, 1);
});
