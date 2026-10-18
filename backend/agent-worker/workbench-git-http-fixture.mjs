/** Disposable stock Git CGI fixture; production still uses the native host adapter. */
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
const exec = promisify(execFile);
export async function gitHttpFixture(format) {
  const directory = await mkdtemp('/tmp/workbench-git-remote-');
  const seed = `${directory}/seed`, remote = `${directory}/git/fixture.git`;
  const command = async (cwd, ...args) => (await exec('git', args, { cwd })).stdout.trim();
  try {
    await mkdir(seed); await mkdir(`${directory}/git`);
    await command(seed, 'init', `--object-format=${format}`, '--initial-branch=main', '.');
    await writeFile(`${seed}/note`, 'cloud starting point');
    await command(seed, 'add', '.');
    await command(seed, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Seed');
    const base = await command(seed, 'rev-parse', 'HEAD');
    await command(directory, 'clone', '--bare', seed, remote);
    return { base, command: (...args) => command(remote, ...args), close: () => rm(directory, {recursive:true, force:true}),
      exchange: frame => new Promise((resolve, reject) => {
        const url = new URL(frame.path, 'http://127.0.0.1'), body = Buffer.from(frame.body, 'base64');
        const child = spawn('git', ['http-backend'], { cwd: directory, env: {
          PATH: '/usr/local/bin:/usr/bin:/bin', HOME: '/nonexistent', GIT_CONFIG_NOSYSTEM: '1',
          GIT_CONFIG_GLOBAL: '/dev/null', GIT_PROJECT_ROOT: directory, GIT_HTTP_EXPORT_ALL: '1',
          GIT_PROTOCOL: frame.protocol || '', REQUEST_METHOD: frame.method, PATH_INFO: url.pathname,
          QUERY_STRING: url.search.slice(1), REMOTE_USER: 'fixture', REMOTE_ADDR: '127.0.0.1',
          CONTENT_TYPE: `application/x-${url.pathname.split('/').at(-1)}-request`, CONTENT_LENGTH: String(body.length),
        }, stdio: ['pipe','pipe','pipe'] });
        const output = []; let stderr = '';
        child.stdout.on('data', data => output.push(data)); child.stderr.on('data', data => {stderr += data;});
        child.once('error', reject);
        child.once('exit', code => {
          if (code) return reject(new Error(`Stock Git CGI ${code}: ${stderr}`));
          const response = Buffer.concat(output), split = response.indexOf('\r\n\r\n');
          if (split < 0) return reject(new Error('Missing Git CGI headers'));
          const headers = response.subarray(0, split).toString();
          resolve({ status: Number(headers.match(/^Status: (\d+)/im)?.[1] || 200),
            content_type: headers.match(/^Content-Type: (.+)\r?$/im)?.[1].trim() || 'application/octet-stream',
            body: response.subarray(split+4).toString('base64') });
        });
        child.stdin.end(body);
      }) };
  } catch (error) { await rm(directory, {recursive:true, force:true}); throw error; }
}
