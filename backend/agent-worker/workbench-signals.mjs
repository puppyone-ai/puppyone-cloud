/** Executed as a single task UID; kernel permissions fence PID reuse races. */
import { readdir, readFile } from 'node:fs/promises';
const signal = process.argv[2], uid = process.getuid();
if (uid < 20000 || uid > 59999 || !['SIGKILL', 'SIGSTOP', 'SIGCONT'].includes(signal))
  throw new Error('Invalid task signal identity');
for (let attempt = 0; attempt < 32; attempt++) {
  let found = false;
  for (const name of await readdir('/proc')) {
    if (!/^\d+$/.test(name) || Number(name) === process.pid) continue;
    try {
      const state = await readFile(`/proc/${name}/status`, 'utf8');
      if (Number(state.match(/^Uid:\s+(\d+)/m)?.[1]) !== uid || /^State:\s+Z\b/m.test(state)) continue;
      if (signal === 'SIGSTOP' && /^State:\s+T\b/m.test(state)) continue;
      process.kill(Number(name), signal); found = true;
    } catch (error) { if (!['ENOENT', 'ESRCH', 'EPERM', 'EACCES'].includes(error.code)) throw error; }
  }
  if (!found || signal === 'SIGCONT') process.exit(0);
  await new Promise(resolve => setTimeout(resolve, 20));
}
throw new Error('Task processes did not quiesce');
