/** Test transport only. Production controller/Pi/tools execute; only model tokens are fixtures. */
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';

export function scriptedTask(slot, workbench, generation, { command, execution = randomUUID(), lease = 60000 } = {}) {
  if (typeof command !== 'string') throw new Error('A deterministic tool command is required');
  const child = spawn('node', ['/opt/puppyone-agent/workbench-controller.mjs', String(slot), workbench, generation, execution],
    { env: { ...process.env, PUPPYONE_WORKBENCH_TASK_LEASE_MS: String(lease) }, stdio: ['pipe','pipe','pipe'] });
  const waiting = new Map(), state = { ready: false, finished: false, errors: [], requests: 0, results: [] };
  let errors = '';
  child.stderr.on('data', data => { errors += data; });
  const send = frame => child.stdin.write(JSON.stringify(frame) + '\n');
  createInterface({ input: child.stdout }).on('line', line => {
    const frame = JSON.parse(line);
    if (frame.type === 'control_result') {
      const reply = waiting.get(frame.id); waiting.delete(frame.id);
      frame.error ? reply?.reject(new Error(frame.error)) : reply?.resolve(frame.result);
    } else if (frame.type === 'ready') state.ready = true;
    else if (frame.type === 'finished') state.finished = true;
    else if (['failed','disconnected'].includes(frame.type)) state.errors.push(frame);
    else if (['tool_start','tool_end','checkpoint'].includes(frame.type)) {
      if (frame.type === 'tool_end') state.results.push(frame.result);
      send({ type: 'reply', id: frame.id, allow: true });
    } else if (frame.type === 'model_request') {
      const first = state.requests++ === 0;
      const delta = first ? { role: 'assistant', tool_calls: [{ index: 0, id: 'call_fixture', type: 'function',
        function: { name: 'bash', arguments: JSON.stringify({ command }) } }] } : { role: 'assistant', content: 'Done.' };
      const chunk = { id: 'fixture', object: 'chat.completion.chunk', created: 1, model: 'fixture',
        choices: [{ index: 0, delta, finish_reason: null }] };
      send({ type: 'model_chunk', id: frame.id, frame: chunk });
      send({ type: 'model_chunk', id: frame.id, frame: { ...chunk,
        choices: [{ index: 0, delta: {}, finish_reason: first ? 'tool_calls' : 'stop' }],
        usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } } });
      send({ type: 'model_end', id: frame.id });
    }
  });
  child.on('exit', code => {
    for (const waiter of waiting.values()) waiter.reject(new Error(`Controller exit ${code}: ${errors}`));
    waiting.clear();
  });
  const control = (action, value) => new Promise((resolve, reject) => {
    const id = randomUUID(); waiting.set(id, { resolve, reject }); send({ type: 'control', id, action, value });
  });
  const timer = setInterval(() => { if (child.exitCode === null) send({ type: 'control', id: 'lease', action: 'heartbeat' }); }, 250);
  child.once('exit', () => clearInterval(timer));
  return { child, state, control, send, execution, expire: () => clearInterval(timer) };
}

export async function poll(fn) {
  const until = Date.now() + 15000;
  while (Date.now() < until) { if (await fn()) return; await new Promise(resolve => setTimeout(resolve, 30)); }
  throw new Error('Timed out waiting for real sandbox process');
}
