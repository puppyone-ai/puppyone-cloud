/** Disposable Linux image acceptance: a stopped SSH command leaves nothing on the machine. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import { SHELL_HOME, STATE } from './workbench-task.mjs';
import { exists as stat } from './git.mjs';

const exists = async path => Boolean(await stat(path));
import { initializeWorkbench } from './workbench-initialize.mjs';
import { poll } from './workbench-test-task.mjs';

const enabled = process.env.PUPPYONE_WORKBENCH_ACCEPTANCE === '1' && process.platform === 'linux' && process.getuid() === 0;

function shell(uid, workbench, generation) {
  const child = spawn('node', ['/opt/puppyone-agent/workbench-shell.mjs', String(uid), workbench, generation, randomUUID()],
    { stdio: ['pipe', 'pipe', 'pipe'] });
  const waiting = new Map();
  createInterface({ input: child.stdout }).on('line', line => {
    const frame = JSON.parse(line);
    if (frame.type !== 'control_result') return;
    const reply = waiting.get(frame.id); waiting.delete(frame.id);
    frame.error ? reply?.reject(new Error(frame.error)) : reply?.resolve(frame.result);
  });
  child.on('exit', code => { for (const reply of waiting.values()) reply.reject(new Error(`Shell exit ${code}`)); });
  const control = (action, value) => new Promise((resolve, reject) => {
    const id = randomUUID(); waiting.set(id, { resolve, reject });
    child.stdin.write(JSON.stringify({ type: 'control', id, action, value }) + '\n');
  });
  return { child, control };
}

async function holders(gid) {
  const found = [];
  for (const name of await readdir('/proc')) {
    if (!/^\d+$/.test(name)) continue;
    try {
      const status = await readFile(`/proc/${name}/status`, 'utf8');
      if (new RegExp(`^Gid:\\s+${gid}\\s`, 'm').test(status) && !/^State:\s+Z\b/m.test(status)) found.push(name);
    } catch { /* gone */ }
  }
  return found;
}

// A machine is initialized once, as a real one is; every command shares it.
let initialized;
function workbench() {
  initialized ??= (async () => {
    const binding = { workbench: randomUUID(), generation: randomUUID() };
    await initializeWorkbench(binding, { project_id: 'fixture', object_format: 'sha1', target_ref: 'refs/heads/main',
      base_oid: null }, () => { throw new Error('Empty initialization must not fetch'); });
    return binding;
  })();
  return initialized;
}

test('a command in the wider range runs, and once stopped leaves no process or record', { skip: !enabled, timeout: 60000 }, async () => {
  const { workbench: id, generation } = await workbench();
  // Past 59999 and nogroup (65534): the first identity of the second range.
  const uid = 100000, home = SHELL_HOME;
  const command = shell(uid, id, generation);
  try {
    assert.equal((await command.control('capabilities')).workspace_shell, 1);
    await command.control('start', { term: '', size: [80, 24], command:
      'test "$(id -G)" = 100000; echo kept > "$HOME/note"; mkdir -p "$HOME/locked/inner"; ' +
      'touch "$HOME/locked/inner/file"; chmod 500 "$HOME/locked"; ' +
      "setsid sh -c 'exec sleep 120' >/dev/null 2>&1 & exec sleep 120" });
    await poll(async () => (await exists(`${home}/note`)) && (await holders(uid)).length >= 2);
    assert.equal(await exists(`${STATE}/task-${uid}.json`), true);

    await command.control('stop');
    assert.deepEqual(await holders(uid), []);
    // Its control records are gone; the home is the machine's and stays.
    assert.equal(await exists(`${home}/note`), true);
    for (const name of [`task-${uid}.json`, `task-${uid}.started`, `task-${uid}.revoked`, `stop-${uid}.lock`])
      assert.equal(await exists(`${STATE}/${name}`), false, name);
    await assert.rejects(command.control('start', { term: '', size: [80, 24], command: 'true' }), /not startable/);
  } finally {
    command.child.kill('SIGKILL');
  }
});

test('a stop before any command still forbids a later start', { skip: !enabled, timeout: 60000 }, async () => {
  const { workbench: id, generation } = await workbench();
  const command = shell(100001, id, generation);
  try {
    await command.control('stop');
    await assert.rejects(command.control('start', { term: '', size: [80, 24], command: 'true' }), /not startable/);
  } finally {
    command.child.kill('SIGKILL');
  }
});

test('an identity that could share a group the image has is refused', { skip: !enabled, timeout: 60000 }, async () => {
  const { workbench: id, generation } = await workbench();
  for (const uid of [65534, 99999, 2000000001]) {
    const command = shell(uid, id, generation);
    await poll(() => command.child.exitCode !== null);
    assert.notEqual(command.child.exitCode, 0, String(uid));
  }
});

test('every command on a machine shares one home, so settings carry to the next', { skip: !enabled, timeout: 60000 }, async () => {
  const { workbench: id, generation } = await workbench();
  const run = async (uid, script) => {
    const command = shell(uid, id, generation);
    try {
      await command.control('start', { term: '', size: [80, 24], command: script });
      await poll(() => command.child.exitCode !== null || exists(`${SHELL_HOME}/.done-${uid}`));
      await command.control('stop');
    } finally {
      command.child.kill('SIGKILL');
    }
  };
  await run(100010, `git config --global user.name 'Agent Fixture' && echo "$HOME" > "$HOME/.done-100010"`);
  await run(100011, `git config --global user.name > "$HOME/seen" && touch "$HOME/.done-100011"`);
  assert.equal((await readFile(`${SHELL_HOME}/seen`, 'utf8')).trim(), 'Agent Fixture');
  assert.equal((await readFile(`${SHELL_HOME}/.done-100010`, 'utf8')).trim(), SHELL_HOME);
});

