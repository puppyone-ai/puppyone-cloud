/** Real controller protocol, without starting model processes or touching host paths. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { once } from 'node:events';

async function controller(t, args = []) {
  const child = spawn(process.execPath, [new URL('./controller.mjs', import.meta.url).pathname, ...args],
    { stdio: ['pipe', 'pipe', 'pipe'] });
  const responses = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
  t.after(async () => { const done = once(child, 'exit'); child.kill(); await done; });
  let id = 0;
  return async (action, value) => {
    const identity = String(++id);
    child.stdin.write(JSON.stringify({ type: 'control', id: identity, action, value }) + '\n');
    const next = await responses.next();
    assert.equal(next.done, false);
    const frame = JSON.parse(next.value);
    assert.equal(frame.id, identity);
    return frame;
  };
}

test('provider-managed controller has no local snapshot or restore capability', { timeout: 10000 }, async t => {
  const control = await controller(t);
  assert.deepEqual((await control('capabilities')).result,
    { workspace_lifecycle: 1, recovery_version: 1, recovery_backend: 'provider-snapshot' });
  assert.match((await control('snapshot')).error, /Use the provider snapshot API/);
  assert.match((await control('prepare', { restore: '00000000-0000-0000-0000-000000000000' })).error,
    /Local recovery is disabled/);
  assert.equal((await control('capabilities')).result.recovery_backend, 'provider-snapshot');
});

test('only the Docker launcher enables the volume recovery capability', { timeout: 10000 }, async t => {
  const control = await controller(t, ['--docker-recovery']);
  assert.deepEqual((await control('capabilities')).result,
    { workspace_lifecycle: 1, recovery_version: 1, recovery_backend: 'docker-volume' });
});
