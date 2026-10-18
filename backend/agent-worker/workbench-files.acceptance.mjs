/** A2 prerequisite, NOT full provider/product acceptance. Shared native file identity regression. */
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

test('native file channel watches Bash saves before publication and rejects stale/confined writes', {timeout: 60000}, async () => {
  const {spawn} = await import('node:child_process');
  const {createInterface} = await import('node:readline');
  const workbench = randomUUID(), generation = randomUUID(), waiting = new Map();
  let child, changes = 0;
  try {
    await initializeWorkbench({workbench, generation}, {project_id:'fixture', object_format:'sha1',
      target_ref:'refs/heads/workspaces/fixture', base_oid:null}, () => {throw new Error('No Git network expected');});
    child = spawn('node', ['/opt/puppyone-agent/workbench-files.mjs',workbench,generation], {stdio:['pipe','pipe','inherit']});
    createInterface({input:child.stdout}).on('line', line => {
      const frame = JSON.parse(line);
      if (frame.type === 'workspace_changed') changes++;
      if (frame.type === 'control_result') {const done = waiting.get(frame.id); waiting.delete(frame.id); frame.error ? done.reject(new Error(frame.error)) : done.resolve(frame.result);}
    });
    const call = (action,value) => new Promise((resolve,reject) => {
      const id = randomUUID(); waiting.set(id,{resolve,reject}); child.stdin.write(JSON.stringify({type:'control',id,action,value})+'\n');
    });
    const file = value => call('files',value);
    await call('watch');
    await exec('sh', ['-c', "umask 077; printf agent-saving > .temporary; mv .temporary file.txt"], {cwd:ROOT,uid:1000,gid:1000});
    await poll(() => changes > 0);
    const read = await file({action:'read',path:'file.txt'});
    assert.equal(Buffer.from(read.result.content_base64,'base64').toString(),'agent-saving');
    // No finalize/commit/push was called: live access is independent of Git.
    await assert.rejects(exec('git',['rev-parse','--verify','HEAD'],{cwd:ROOT,uid:1000,gid:1000}));
    await exec('sh',['-c', 'printf agent-newer > file.txt'],{cwd:ROOT,uid:1000,gid:1000});
    const stale = await file({action:'write',path:'file.txt',expected_revision:read.result.revision,content_base64:Buffer.from('stale human').toString('base64')});
    assert.equal(stale.error,'file_conflict');
    assert.equal(Buffer.from((await file({action:'read',path:'file.txt'})).result.content_base64,'base64').toString(),'agent-newer');
    await exec('ln',['-s','/etc/passwd','outside'],{cwd:ROOT,uid:1000,gid:1000});
    for (const path of ['../etc/passwd','.git/config','outside']) assert.ok((await file({action:'read',path})).error);
    assert.ok((await file({action:'mkdir',path:'folder'})).result);
    assert.ok((await file({action:'move',path:'file.txt',destination:'folder/moved.txt'})).result);
    assert.ok((await file({action:'remove',path:'folder'})).result);
    assert.equal((await file({action:'read',path:'folder/moved.txt'})).error,'file_not_found');
  } finally {
    child?.stdin.end(); if (child) await poll(() => child.exitCode !== null);
    await rm(ROOT,{recursive:true,force:true}); await rm(CONTROL_ROOT,{recursive:true,force:true});
  }
});
