/** Real shared controller -> task-UID Git -> ordinary user, in an owned Linux image. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';
import { once } from 'node:events';
import { readFile, rm } from 'node:fs/promises';
import { gitControl } from './workbench-git-layout.mjs';
import { gitHttpFixture } from './workbench-git-http-fixture.mjs';
import { CONTROL_ROOT } from './workbench-task.mjs';
const exec = promisify(execFile), ROOT = '/workspace/repo';
const enabled = process.env.PUPPYONE_WORKBENCH_ACCEPTANCE === '1' && process.platform === 'linux' && process.getuid() === 0;
function connection(workbench, generation, uid, remote) {
  const child = spawn('node', ['/opt/puppyone-agent/workbench-controller.mjs', String(uid), workbench, generation, randomUUID()],
    { stdio: ['pipe', 'pipe', 'pipe'] });
  const waiting = new Map(); let stderr = '';
  child.stderr.on('data', value => { stderr += value; });
  createInterface({ input: child.stdout }).on('line', value => {
    const frame = JSON.parse(value);
    if (frame.type === 'git_http' && remote) {
      remote.exchange(frame).then(result => child.stdin.write(JSON.stringify({type:'reply',id:frame.id,...result})+'\n'),
        error => child.stdin.write(JSON.stringify({type:'reply',id:frame.id,error:error.message})+'\n'));
    } else if (frame.type === 'control_result') {
      const request = waiting.get(frame.id); waiting.delete(frame.id);
      frame.error ? request?.reject(new Error(frame.error)) : request?.resolve(frame.result);
    }
  });
  child.on('exit', code => { for (const waiter of waiting.values()) waiter.reject(new Error(`Control exit ${code}: ${stderr}`)); });
  return { child, control: (action, value) => new Promise((resolve, reject) => {
    const id = randomUUID(); waiting.set(id, { resolve, reject });
    child.stdin.write(JSON.stringify({ type: 'control', id, action, value })+'\n');
  }) };
}
async function user(uid, source) {
  return exec('node', ['--input-type=module', '-e', source], { uid, gid: 1000, cwd: ROOT,
    env: { PATH: '/usr/local/bin:/usr/bin:/bin', HOME: '/home/node' }, timeout: 20000 });
}
async function fixture(fn, format = 'sha1', remote = null) {
  const identity = [randomUUID(), randomUUID()], clients = [];
  try {
    for (const uid of [20000, 20001]) {
      const client = connection(...identity, uid, remote); clients.push(client);
      await client.control('prepare', { project_id: 'fixture', object_format: format, target_ref: 'refs/heads/main', base_oid: remote?.base || null });
    }
    await fn(clients);
  } finally {
    for (const { child } of clients) if (child.exitCode === null) { child.stdin.end(); await once(child, 'exit'); }
    await rm(ROOT, { recursive: true, force: true });
    await rm(CONTROL_ROOT, { recursive: true, force: true });
  }
}

for (const format of ['sha1', 'sha256']) {
test(`${format}: shared inspect/finalize preserves config and leaves ordinary Git usable`,
  { skip: !enabled, timeout: 30000 }, () => fixture(async clients => {
    await user(20000, `import {writeFile} from 'node:fs/promises';
      await writeFile('.git/config', '[core]\\nrepositoryformatversion=0\\nbare=false\\nhooksPath=/workspace/repo/hooks\\n[user]\\nname=Workspace User\\nemail=user@example.invalid\\n');
      await writeFile('note', 'shared');`);
    const before = await readFile(`${ROOT}/.git/config`, 'utf8');
    await clients[1].control('inspect');
    assert.equal(await readFile(`${ROOT}/.git/config`, 'utf8'), before);
    const saved = await clients[0].control('finalize', { message: 'Captured shared contents' });
    assert.ok(saved.tip);
    assert.equal(await readFile(`${ROOT}/.git/config`, 'utf8'), before);
    for (const uid of [20001, 1001]) {
      const result = await user(uid, `import {execFileSync} from 'node:child_process';
        console.log(execFileSync('git', ['-c','safe.directory=/workspace/repo','config','user.name'], {encoding:'utf8'}));
        execFileSync('git', ['-c','safe.directory=/workspace/repo','status','--porcelain']);`);
      assert.equal(result.stdout.trim(), 'Workspace User');
    }
  }, format));
}

test('ordinary terminal Git remains readable throughout trusted inspections',
  { skip: !enabled, timeout: 30000 }, () => fixture(async clients => {
    const reader = user(1001, `import {execFileSync} from 'node:child_process';
      let failures=0;
      for(let i=0;i<150;i++) { try {execFileSync('git',['-c','safe.directory=/workspace/repo','status','--porcelain'], {stdio:'pipe'});}
        catch(error) {if(String(error.stderr).includes('Permission denied')) failures++; else throw error;} }
      console.log(failures);`);
    for (let i = 0; i < 20; i++) await clients[i % 2].control('inspect');
    assert.equal((await reader).stdout.trim(), '0', 'inspection temporarily made .git/config private');
  }));

test('concurrent hostile Git config cannot execute code or alter the root-owned control view',
  { skip: !enabled, timeout: 30000 }, () => fixture(async clients => {
    await user(20001, `import {writeFile,chmod} from 'node:fs/promises';
      await writeFile('attack.sh', '#!/bin/sh\\ntouch /workspace/repo/PWNED\\ncat\\n'); await chmod('attack.sh',0o755);
      await writeFile('.gitattributes','*.txt filter=attack\\n');
      await writeFile('note.txt','shared contents');`);
    const config = '[include]\\npath=/workspace/repo/.git/attack.conf\\n';
    const payload = '[filter "attack"]\\nclean=/workspace/repo/attack.sh\\nrequired=true\\n[core]\\nfsmonitor=/workspace/repo/attack.sh\\nhooksPath=/workspace/repo\\n';
    const attacker = user(20001, `import {writeFile,appendFile,rm} from 'node:fs/promises';
      import assert from 'node:assert/strict';
      const control=${JSON.stringify(gitControl('sha1') + '/config')};
      await assert.rejects(appendFile(control,'[include]\\npath=evil\\n'), {code:'EACCES'});
      await assert.rejects(rm(control), {code:'EACCES'});
      await writeFile('.git/attack.conf', ${JSON.stringify(payload)}.replaceAll('\\\\n','\\n'));
      for(let i=0;i<300;i++) { await writeFile('.git/config', ${JSON.stringify(config)}.replaceAll('\\\\n','\\n'));
        await new Promise(resolve=>setTimeout(resolve,2)); }`);
    for (let i=0;i<5;i++) await clients[0].control('finalize', {message:'No untrusted config execution'});
    await attacker;
    await assert.rejects(readFile(`${ROOT}/PWNED`), {code:'ENOENT'});
    assert.equal(await readFile(`${ROOT}/.git/config`,'utf8'), config.replaceAll('\\n','\n'));
  }));

for (const format of ['sha1', 'sha256']) {
  test(`${format}: actual shared control fetches, commits and retries the original candidate over stock Git HTTP`,
    { skip: !enabled, timeout: 30000 }, async () => {
      const remote = await gitHttpFixture(format);
      try {
        await fixture(async clients => {
          assert.equal(await readFile(`${ROOT}/note`, 'utf8'), 'cloud starting point');
          await user(20000, `await import('node:fs/promises').then(fs=>fs.writeFile('note','candidate A'));`);
          const first = await clients[0].control('finalize', {message:'Run A'});
          await user(20001, `await import('node:fs/promises').then(fs=>fs.writeFile('note','candidate B'));`);
          const second = await clients[1].control('finalize', {message:'Run B'});
          await user(1001, `await import('node:fs/promises').then(fs=>fs.writeFile('note','human unsaved'));`);
          await clients[0].control('push', {candidate:first.tip});
          await clients[0].control('push', {candidate:first.tip});
          assert.equal(await remote.command('rev-parse','main'), first.tip);
          assert.equal(await remote.command('show','main:note'), 'candidate A');
          assert.equal((await clients[1].control('inspect')).tip, second.tip);
          assert.equal(await readFile(`${ROOT}/note`, 'utf8'), 'human unsaved');
        }, format, remote);
      } finally { await remote.close(); }
    });
}
