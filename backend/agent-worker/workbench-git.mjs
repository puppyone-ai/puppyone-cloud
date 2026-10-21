/** All filesystem access to the user-mutable .git runs WITHOUT root privilege. */
import { sharedGit } from './shared-git.mjs';
if (process.getuid() !== 1000 && (process.getuid() < 20000 || process.getuid() > 59999))
  throw new Error('Invalid workbench Git file identity');
process.umask(0o002);
let input = '';
for await (const chunk of process.stdin) {
  input += chunk;
  if (Buffer.byteLength(input) > 65536) throw new Error('Git command input too large');
}
const { action, config, value, remote } = JSON.parse(input);
const transport = process.env.GIT_CONFIG_VALUE_0 ? {
  GIT_CONFIG_COUNT: '1', GIT_CONFIG_KEY_0: 'http.extraHeader', GIT_CONFIG_VALUE_0: process.env.GIT_CONFIG_VALUE_0,
} : {};
const result = await sharedGit(action, config, value, remote, transport);
process.stdout.write(JSON.stringify(result));
