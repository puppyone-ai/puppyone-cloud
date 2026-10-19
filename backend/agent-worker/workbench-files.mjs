/** Backend-only native file channel. No Git publication and no VM lifecycle. */
import { spawn } from 'node:child_process';
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
let helper, active, buffer = '', failed = false, closing = false;
function helperFailed() {
  failed = true;
  active?.reject(new Error('Workspace file operation unavailable'));
  active = undefined;
  helper?.kill('SIGKILL');
  if (!closing) { process.exitCode = 1; setImmediate(close); }
}
function files(value) {
  if (failed || closing) return Promise.reject(new Error('Workspace file channel unavailable'));
  if (!helper) {
    // One unprivileged helper per channel; requests still reopen the confined
    // root and use native descriptors. No file bytes or revisions are cached.
    helper = spawn('python3', ['-u', '/opt/puppyone-agent/workbench-files.py', '--stream'], {
      uid: 1000, gid: 1000, cwd: ROOT, stdio: ['pipe', 'pipe', 'ignore'],
      env: {PATH: '/usr/local/bin:/usr/bin:/bin'},
    });
    helper.on('error', helperFailed);
    helper.on('exit', () => { if (!closing) helperFailed(); });
    helper.stdin.on('error', helperFailed);
    helper.stdout.setEncoding('utf8');
    helper.stdout.on('data', chunk => {
      buffer += chunk;
      if (Buffer.byteLength(buffer) > 16 * 1024 * 1024) return helperFailed();
      const end = buffer.indexOf('\n');
      if (end < 0) return;
      if (!active || end !== buffer.length - 1) return helperFailed();
      try {
        const result = JSON.parse(buffer.slice(0, end)); buffer = '';
        const waiter = active; active = undefined; waiter.resolve(result);
      } catch { helperFailed(); }
    });
  }
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(helperFailed, 20000);
    active = {
      resolve: value => { clearTimeout(timeout); resolve(value); },
      reject: error => { clearTimeout(timeout); reject(error); },
    };
    helper.stdin.write(JSON.stringify(value) + '\n');
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
function close() {
  if (closing) return;
  closing = true; watcher?.close(); clearTimeout(timer); helper?.kill('SIGKILL');
  active?.reject(new Error('Workspace file channel closed')); active = undefined;
  control.close(); pending.finally(() => process.exit());
}
process.stdin.on('end', close);
