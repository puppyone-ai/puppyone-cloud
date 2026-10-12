import test from 'node:test';
import assert from 'node:assert/strict';
import { gitRelay } from './git-relay.mjs';

test('a different shared task cannot use an active Git relay', async () => {
  let calls = 0;
  const relay = await gitRelay(async () => {
    calls++;
    return { status: 200, content_type: 'application/octet-stream', body: Buffer.from('authorized').toString('base64') };
  }, 'project', { authenticated: true });
  try {
    await relay.run(async () => {
      const path = relay.url + '/info/refs?service=git-upload-pack';
      assert.equal((await fetch(path)).status, 503);
      assert.equal((await fetch(path, { headers: { Authorization: 'Bearer wrong-task' } })).status, 503);
      assert.equal(calls, 0);
      assert.equal(relay.transport.GIT_CONFIG_KEY_0, 'http.extraHeader');
      const value = relay.transport.GIT_CONFIG_VALUE_0.replace(/^Authorization: /, '');
      assert.equal(await (await fetch(path, { headers: { Authorization: value } })).text(), 'authorized');
      assert.equal(calls, 1);
    });
    assert.equal((await fetch(relay.url + '/info/refs')).status, 503);
  } finally { relay.close(); }
});
