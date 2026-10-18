/** Backend-only native file channel. No Git publication and no VM lifecycle. */
import { execFile } from 'node:child_process';
import { watch } from 'node:fs';
import { channel } from './protocol.mjs';
import { ROOT } from './git.mjs';
import { UUID, requireWorkspace } from './workbench-state.mjs';
const [workbench, generation] = process.argv.slice(2);
if (process.getuid() !== 0 || ![workbench, generation].every(value => UUID.test(value)))
  throw new Error('Invalid file channel binding');
const binding = {workbench, generation};
await requireWorkspace(binding);
const control = channel();
let watcher, timer, pending = Promise.resolve();
function files(value) {
  return new Promise((resolve, reject) => {
    const child = execFile('python3', ['/opt/puppyone-agent/workbench-files.py'], {
      uid: 1000, gid: 1000, cwd: ROOT, timeout: 20000, maxBuffer: 16 * 1024 * 1024,
      env: {PATH: '/usr/local/bin:/usr/bin:/bin'},
    }, (error, stdout) => {
      if (error) reject(new Error('Workspace file operation unavailable'));
      else { try { resolve(JSON.parse(stdout)); } catch (cause) { reject(cause); } }
    });
    child.stdin.end(JSON.stringify(value));
  });
}
control.dispatch(frame => {
  pending = pending.then(async () => {
    try {
      if (frame.type !== 'control') throw new Error('Files channel accepts control only');
      await requireWorkspace(binding);
      let result;
      if (frame.action === 'files') result = await files(frame.value);
      else if (frame.action === 'watch') {
        if (!watcher) {
          watcher = watch(ROOT, {recursive: true}, () => {
            if (!timer) timer = setTimeout(() => {
              timer = undefined;
              control.send({type: 'workspace_changed', generation});
            }, 100);
          });
          watcher.on('error', () => {
            control.send({type: 'workspace_changed', generation, reset: true});
            process.exitCode = 1; close();
          });
        }
        result = {};
      } else throw new Error('Unknown file channel operation');
      control.send({type: 'control_result', id: frame.id, result});
    } catch (error) {
      control.send({type: 'control_result', id: frame.id, error: error.message});
    }
  });
});
function close() { watcher?.close(); clearTimeout(timer); control.close(); pending.finally(() => process.exit()); }
process.stdin.on('end', close);
