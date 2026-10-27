/** Sandbox composition and stdin/stdout transport for the Pi loop. */
import { createInterface } from 'node:readline';
import { createPiLoop } from './loop.mjs';
import { localFilesystem } from './filesystem.mjs';
import { localExecution } from './execution.mjs';

const root = process.cwd();
const send = value => process.stdout.write(`${JSON.stringify(value)}\n`);
const loop = createPiLoop({ root, agentDir: process.env.PUPPYONE_AGENT_DIR || '/tmp/puppyone-agent',
  send, filesystem: localFilesystem(root), execution: localExecution(root) });
const lines = createInterface({ input: process.stdin });
lines.on('line', line => {
  try {
    if (Buffer.byteLength(line) > 192 * 1024 * 1024) throw new Error('Control frame too large');
    loop.receive(JSON.parse(line));
  } catch (error) { send({ type: 'failed', error: error.message }); }
});
lines.on('close', () => { loop.close(); setTimeout(() => process.exit(1), 1000).unref(); });
