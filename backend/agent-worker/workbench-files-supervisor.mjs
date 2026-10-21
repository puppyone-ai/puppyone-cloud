/** One VM-owned file service. Transport processes only attach to its socket. */
import { fork } from 'node:child_process';
import { unlink } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

export function superviseFiles() {
  let child, restart;
  let stopping = false, failures = 0;
  const stop = () => {
    stopping = true;
    clearTimeout(restart);
    child?.kill('SIGTERM');
  };
  process.once('SIGTERM', stop);
  process.once('SIGINT', stop);

  return new Promise((resolve, reject) => {
    const fail = error => {
      // A fatal supervisor error after the first ready event must still stop
      // the VM entrypoint; rejecting an already resolved Promise does nothing.
      console.error('Workspace file supervisor failed:', error.message);
      process.exitCode = 1;
      stop();
      reject(error);
    };
    const start = async () => {
      try {
        await unlink('/run/puppyone-workbench/files.sock').catch(error => {
          if (error.code !== 'ENOENT') throw error;
        });
        if (stopping) return;
        const running = fork(new URL('./workbench-files.mjs', import.meta.url), ['--serve'], {
          stdio: ['ignore', 'inherit', 'inherit', 'ipc']
        });
        child = running;
        const started = Date.now();
        const deadline = setTimeout(() => running.kill('SIGKILL'), 15000);
        running.once('message', value => {
          if (value?.type === 'ready') {
            clearTimeout(deadline);
            resolve();
          }
        });
        running.once('error', fail);
        running.once('exit', () => {
          clearTimeout(deadline);
          if (stopping) return;
          failures = Date.now() - started > 30000 ? 0 : failures + 1;
          const backoff = Math.min(30000, 250 * 2 ** Math.min(failures, 7));
          restart = setTimeout(() => { void start(); }, backoff);
        });
      } catch (error) { fail(error); }
    };
    void start();
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await superviseFiles();
}
