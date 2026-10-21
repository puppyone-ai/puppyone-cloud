/** Disposable Linux image acceptance. No network, external model or host mounts. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { scriptedTask, poll } from './workbench-test-task.mjs';
import { randomUUID } from 'node:crypto';
import { readFile, writeFile, rm } from 'node:fs/promises';
import { CONTROL_ROOT, STATE, withWorkbenchGitLock } from './workbench-task.mjs';
import { exists } from './git.mjs';
import { initializeWorkbench } from './workbench-initialize.mjs';
const ROOT = '/workspace/repo';
const enabled = process.env.PUPPYONE_WORKBENCH_ACCEPTANCE === '1' && process.platform === 'linux' && process.getuid() === 0;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function content(name) { try { return await readFile(`${ROOT}/${name}`, 'utf8'); } catch (e) { if (e.code !== 'ENOENT') throw e; return ''; } }

function taskConnection(slot, workbench, generation, options = {}) {
  // Slot names distinguish tasks, not filesystem tenants. Keep unprivileged/no-new-privs assertions.
  const command = `set -eu; test "$(id -u)" = 1000; test "$(id -G)" = ${slot}; ` +
    `grep -q '^NoNewPrivs:[[:space:]]*1$' /proc/$$/status; ` +
    `printf ready > ${slot}.ready; while [ ! -e go ]; do sleep .05; done; ` +
    `printf '${slot}\\n' >> shared.txt; setsid sh -c 'echo $$ > ${slot}.detached; exec sleep 120' >/dev/null 2>&1 & ` +
    `while :; do date +%s%N > ${slot}.alive; sleep .1; done`;
  return scriptedTask(slot, workbench, generation, { ...options, command });
}

for (const ending of ['stop','finalize','disconnect','lease_expiry','duplicate_cleanup','publication_cleanup_race']) {
  test(`real concurrent Pi tasks, PTY and preview survive ${ending}`, { skip: !enabled, timeout: 60000 }, async () => {
    const workbench = randomUUID(), generation = randomUUID(), tasks = [], children = [];
    let unblockGit;
    try {
      process.umask(0o002);
      await initializeWorkbench({ workbench, generation }, { project_id: 'fixture', object_format: 'sha1',
        target_ref: 'refs/heads/main', base_oid: null }, () => { throw new Error('Empty initialization must not fetch'); });
      for (const uid of [20000,20001]) {
        const task = taskConnection(uid, workbench, generation, { lease: ending === 'lease_expiry' && uid === 20000 ? 2000 : 60000 }); tasks.push(task);
        assert.equal((await task.control('capabilities')).shared_workbench, 3);
        await task.control('prepare', { project_id: 'fixture', object_format: 'sha1', target_ref: 'refs/heads/main', base_oid: null, readonly: false });
        task.send({ type: 'start', config: { model: 'fixture', max_tokens: 4096, context_window: 131072,
          readonly: false, prompt: 'Run the fixture command.' } });
      }
      await poll(async () => {
        assert.deepEqual(tasks.flatMap(task => task.state.errors), []);
        return (await content('20000.ready')) && (await content('20001.ready'));
      });
      if (ending === 'stop') {
        const stale = taskConnection(20002, workbench, randomUUID()); tasks.push(stale);
        await assert.rejects(stale.control('prepare', { project_id: 'fixture', object_format: 'sha1',
          target_ref: 'refs/heads/main', base_oid: null, readonly: false }), /generation mismatch/);
        await poll(() => stale.child.exitCode !== null);
        assert.equal(stale.state.requests, 0);
        assert.equal(await content('20002.ready'), '');
        const collision = taskConnection(20000, workbench, generation); tasks.push(collision);
        await assert.rejects(collision.control('capabilities'), /Task identity is already owned/);
        // A disconnected, prepared controller must not resurrect a cleaned task.
        const late = taskConnection(20003, workbench, generation); tasks.push(late);
        await late.control('prepare', { project_id: 'fixture', object_format: 'sha1', target_ref: 'refs/heads/main', base_oid: null });
        const cleanup = taskConnection(20003, workbench, generation, { execution: late.execution }); tasks.push(cleanup);
        await cleanup.control('stop');
        late.send({ type: 'start', config: { model: 'fixture', max_tokens: 4096, context_window: 131072, prompt: 'Must not restart' } });
        await poll(() => late.state.errors.some(frame => /revoked/.test(frame.error)));
        assert.equal(late.state.requests, 0);
        await assert.rejects(late.control('finalize', { message: 'Must not commit after cleanup' }), /revoked/);
      }
      const terminal = spawn('script', ['-qefc', `test "$PWD" = ${ROOT} && test -t 0 && echo PTY_READY; exec cat`, '/dev/null'],
        { cwd: ROOT, uid: 1000, gid: 1000, stdio: ['pipe','pipe','pipe'] }); children.push(terminal);
      let output = ''; terminal.stdout.on('data', data => { output += data; });
      await poll(() => output.includes('PTY_READY'));
      const preview = spawn('node', ['-e', "require('http').createServer((q,s)=>s.end(require('fs').readFileSync('human-unsaved'))).listen(8080,'127.0.0.1',()=>console.log('READY'))"],
        { cwd: ROOT, uid: 1000, gid: 1000, stdio: ['ignore','pipe','pipe'] }); children.push(preview);
      let listening = false; preview.stdout.once('data', () => { listening = true; }); await poll(() => listening);
      await writeFile(`${ROOT}/go`, 'go', { mode: 0o664 });
      await poll(async () => (await content('shared.txt')).includes('20000') && (await content('shared.txt')).includes('20001'));
      await poll(async () => (await content('20000.detached')) && (await content('20001.detached')) && (await content('20001.alive')));
      const before = await content('20001.alive');
      await writeFile(`${ROOT}/human-unsaved`, 'unique current copy', { mode: 0o664 });
      if (ending === 'finalize') {
        const readonly = taskConnection(20002, workbench, generation); tasks.push(readonly);
        await readonly.control('prepare', { object_format: 'sha1', target_ref: 'refs/heads/main', base_oid: null, readonly: true });
        const result = await readonly.control('finalize', { message: 'Must not commit other users files' });
        assert.equal(result.changed, false); assert.equal(result.tip, null);
        assert.equal(await readFile('/workspace/repo/human-unsaved', 'utf8'), 'unique current copy');
      }
      if (ending === 'disconnect') { tasks[0].expire(); tasks[0].child.stdin.end(); await poll(() => tasks[0].child.exitCode !== null); }
      else if (ending === 'lease_expiry') { tasks[0].expire(); await poll(() => tasks[0].child.exitCode !== null); }
      else if (ending === 'publication_cleanup_race') {
        const blocking = withWorkbenchGitLock(() => new Promise(resolve => { unblockGit = resolve; }));
        await poll(() => unblockGit);
        const finalizing = tasks[0].control('finalize', { message: 'Original candidate' });
        await poll(() => exists(`${STATE}/task-20000.model-stopped`));
        const cleanup = taskConnection(20000, workbench, generation, { execution: tasks[0].execution }); tasks.push(cleanup);
        let acknowledged = false;
        const stopping = cleanup.control('stop').then(() => { acknowledged = true; });
        await sleep(200); assert.equal(acknowledged, false, 'cleanup acknowledged while trusted Git was pending');
        unblockGit(); await blocking; await finalizing; await stopping;
        await assert.rejects(tasks[0].control('finalize', { message: 'Late retry' }), /revoked/);
      } else if (ending === 'duplicate_cleanup') {
        const duplicate = taskConnection(20000, workbench, generation, { execution: tasks[0].execution }); tasks.push(duplicate);
        await Promise.all([tasks[0].control('stop'), duplicate.control('stop')]);
      } else await tasks[0].control(ending, { message: 'Shared worktree checkpoint' });
      await poll(async () => (await content('20001.alive')) !== before);
      terminal.stdin.write('PTY_AFTER_TASK_END\n'); await poll(() => output.includes('PTY_AFTER_TASK_END'));
      assert.equal(await (await fetch('http://127.0.0.1:8080')).text(), 'unique current copy');
      assert.equal(await content('human-unsaved'), 'unique current copy');
      for (const uid of [20000,20001]) {
        const pid = (await content(`${uid}.detached`)).trim();
        let state = ''; try { state = await readFile(`/proc/${pid}/status`, 'utf8'); } catch (e) { if (e.code !== 'ENOENT') throw e; }
        assert.equal(Boolean(state) && !/^State:\s+Z/m.test(state), uid === 20001);
      }
      await assert.rejects(tasks[1].control('snapshot'), /workbench lifecycle/);
      await assert.rejects(tasks[1].control('freeze'), /workbench lifecycle/);
      assert.equal(tasks[1].state.requests, 1);
    } finally {
      unblockGit?.();
      for (const task of tasks) {
        task.expire();
        if (task.child.exitCode === null) { task.child.stdin.end(); await poll(() => task.child.exitCode !== null); }
      }
      for (const child of children) { child.kill('SIGKILL'); await poll(() => child.exitCode !== null || child.signalCode !== null); }
      // Only the two fixed data roots in this disposable acceptance image.
      await rm(ROOT, { recursive: true, force: true });
      await rm(CONTROL_ROOT, { recursive: true, force: true });
    }
  });
}
