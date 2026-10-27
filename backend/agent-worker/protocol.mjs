/** Bounded JSON control frames. Model output never grants workspace capabilities. */
import { randomUUID } from 'node:crypto';

export const MAX_FRAME = 192 * 1024 * 1024;

export function channel(input = process.stdin, output = process.stdout, options = {}) {
  const maxFrame = options.maxFrame ?? MAX_FRAME;
  const pending = new Map();
  let sequence = 0, chunks = [], bufferedBytes = 0, handler;

  const send = value => {
    const data = JSON.stringify({ ...value, sequence: sequence + 1 }) + '\n';
    const size = Buffer.byteLength(data);
    if (size > maxFrame) throw new Error('Control frame exceeds limit');
    if (options.maxBuffered && output.writableLength + size > options.maxBuffered) {
      throw new Error('Control output backlog exceeded');
    }
    output.write(data);
    sequence++;
  };

  const request = (type, payload = {}) => new Promise((resolve, reject) => {
    const id = randomUUID();
    pending.set(id, { resolve, reject });
    try { send({ type, id, ...payload }); }
    catch (error) { pending.delete(id); reject(error); }
  });

  const fail = error => {
    if (options.onError) options.onError(error);
    else send({ type: 'failed', error: error.message });
  };

  const dispatchFrame = line => {
    try {
      const frame = JSON.parse(line);
      if (frame.type === 'reply' && pending.has(frame.id)) {
        const waiter = pending.get(frame.id);
        pending.delete(frame.id);
        if (frame.error) waiter.reject(new Error(frame.error));
        else waiter.resolve(frame);
      } else {
        Promise.resolve(handler?.(frame)).catch(fail);
      }
    } catch (error) { fail(error); }
  };

  input.setEncoding('utf8');
  const onData = chunk => {
    let start = 0;
    while (start < chunk.length) {
      const end = chunk.indexOf('\n', start);
      const piece = chunk.slice(start, end < 0 ? chunk.length : end);
      bufferedBytes += Buffer.byteLength(piece);
      if (bufferedBytes + 1 > maxFrame) {
        chunks = []; bufferedBytes = 0;
        fail(new Error('Control frame exceeds limit'));
        return;
      }
      chunks.push(piece);
      if (end < 0) break;
      const line = chunks.join(''); chunks = []; bufferedBytes = 0;
      if (line) dispatchFrame(line);
      start = end + 1;
    }
  };

  const dispatch = callback => {
    handler = callback;
    input.on('data', onData);
  };
  const close = () => {
    for (const waiter of pending.values()) waiter.reject(new Error('Control disconnected'));
    pending.clear();
    input.removeListener('data', onData);
    chunks = []; bufferedBytes = 0;
  };
  return { send, request, dispatch, close };
}
