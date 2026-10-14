/** A2 prerequisite, NOT full provider/product acceptance. Expected red until shared file identity is implemented. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import { stat, rm } from 'node:fs/promises';
import { initializeWorkbench } from './workbench-initialize.mjs';
import { CONTROL_ROOT } from './workbench-state.mjs';
import { scriptedTask, poll } from './workbench-test-task.mjs';

if (process.env.PUPPYONE_WORKBENCH_ACCEPTANCE !== '1' || process.platform !== 'linux' || process.getuid() !== 0)
  throw new Error('Run only inside the owned WorkbenchFileAcceptance image; unsupported environments must not skip green');
const ROOT = '/workspace/repo', exec = promisify(execFile);

for (const reader of ['another-agent', 'ordinary-terminal']) {
  test(`private atomic save remains readable by ${reader} without permission repair`, { timeout: 60000 }, async () => {
    const workbench = randomUUID(), generation = randomUUID(), tasks = [];
    const config = { project_id: 'fixture', object_format: 'sha1', target_ref: 'refs/heads/main', base_oid: null };
    async function run(slot, command) {
      const task = scriptedTask(slot, workbench, generation, { command }); tasks.push(task);
      await task.control('prepare', config);
      task.send({ type: 'start', config: { model: 'fixture', max_tokens: 4096, context_window: 131072,
        readonly: false, prompt: 'Execute the deterministic fixture command.' } });
      await poll(() => { assert.deepEqual(task.state.errors, []); return task.state.finished; });
      return task;
    }
    try {
      process.umask(0o002);
      await initializeWorkbench({ workbench, generation }, config, () => { throw new Error('Unexpected Git network request'); });
      const script = "const fs=require('fs');fs.writeFileSync('.save','private-owner-data',{mode:0o600,flag:'wx'});fs.renameSync('.save','private.txt');console.log('SAVED')";
      const writer = await run(20000, `node -e ${JSON.stringify(script)}`);
      assert.match(JSON.stringify(writer.state.results), /SAVED/);
      assert.equal((await stat(`${ROOT}/private.txt`)).mode & 0o777, 0o600);
      const fileUser = (await stat(ROOT)).uid;
      if (reader === 'another-agent') {
        const other = await run(20001, 'cat private.txt');
        assert.match(JSON.stringify(other.state.results), /private-owner-data/,
          'A second actual Pi Bash tool must read the saved content, not EACCES');
      } else {
        const result = await exec('cat', ['private.txt'], { cwd: ROOT, uid: fileUser, gid: 1000, timeout: 10000 });
        assert.equal(result.stdout, 'private-owner-data');
      }
      const saved = await stat(`${ROOT}/private.txt`);
      assert.equal(saved.uid, fileUser, 'Task isolation cannot introduce a different filesystem tenant');
      assert.equal(saved.mode & 0o777, 0o600, 'No chmod/group-write workaround');
    } finally {
      for (const task of tasks) {
        task.expire();
        if (task.child.exitCode === null) { task.child.stdin.end(); await poll(() => task.child.exitCode !== null); }
      }
      await rm(ROOT, { recursive: true, force: true });
      await rm(CONTROL_ROOT, { recursive: true, force: true });
    }
  });
}
