import test from 'node:test';
import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { channel } from './protocol.mjs';

test('frame limit applies to individual frames, including fragmented Unicode input', () => {
  const input = new PassThrough(), output = new PassThrough(), frames = [], errors = [];
  const control = channel(input, output, { maxFrame: 40, onError: error => errors.push(error) });
  control.dispatch(frame => frames.push(frame));
  const wire = Buffer.from('{"value":"文件"}\n');
  input.write(wire.subarray(0, 11));
  input.write(Buffer.concat([wire.subarray(11), wire, wire]));
  assert.deepEqual(frames, [{ value: '文件' }, { value: '文件' }, { value: '文件' }]);
  assert.equal(errors.length, 0);
  input.write('x'.repeat(40));
  assert.match(errors[0].message, /limit/);
  control.close();
  input.write(wire);
  assert.equal(frames.length, 3);
});

test('failed writes do not consume a sequence or leave pending requests', async () => {
  const input = new PassThrough(), output = new PassThrough();
  const control = channel(input, output, { maxFrame: 128 });
  control.dispatch(() => {});
  await assert.rejects(control.request('too-large', { value: 'x'.repeat(128) }), /limit/);
  control.send({ type: 'healthy' });
  assert.equal(JSON.parse(output.read().toString()).sequence, 1);
  const pending = control.request('waiting');
  control.close();
  await assert.rejects(pending, /disconnected/);
});

test('slow receivers have a bounded output backlog', () => {
  const output = new PassThrough({ highWaterMark: 1 });
  const control = channel(new PassThrough(), output, { maxBuffered: 80 });
  control.send({ type: 'event', payload: 'x'.repeat(10) });
  assert.throws(() => control.send({ type: 'event', payload: 'x'.repeat(10) }), /backlog/);
  control.close();
});
