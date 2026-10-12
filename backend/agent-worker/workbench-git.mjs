/** All filesystem access to the user-mutable .git runs WITHOUT root privilege. */
import { chmod } from 'node:fs/promises';
import { ROOT, prepare, inspect, finalize, push } from './git.mjs';
if (process.getuid() < 20000 || process.getuid() > 59999)
  throw new Error('Invalid workbench Git task identity');
let input = '';
for await (const chunk of process.stdin) {
  input += chunk;
  if (Buffer.byteLength(input) > 65536) throw new Error('Git command input too large');
}
const { action, config, value, remote } = JSON.parse(input);
const transport = process.env.GIT_CONFIG_VALUE_0 ? {
  GIT_CONFIG_COUNT: '1', GIT_CONFIG_KEY_0: 'http.extraHeader', GIT_CONFIG_VALUE_0: process.env.GIT_CONFIG_VALUE_0,
} : {};
let result;
if (action === 'prepare') result = await prepare(ROOT, config, remote, { transport });
else if (action === 'inspect') result = await inspect(ROOT, config);
else if (action === 'finalize') result = await finalize(ROOT, config, value.message);
else if (action === 'push') result = await push(ROOT, config, remote, value.candidate, transport);
else throw new Error('Unknown workbench Git operation');
await chmod(`${ROOT}/.git/config`, 0o660);
process.stdout.write(JSON.stringify(result));
