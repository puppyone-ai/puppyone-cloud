/** Root-owned Git control view. Only config is private; refs/index/objects remain native shared data. */
import { mkdir, chmod, writeFile, readFile, symlink, readlink } from 'node:fs/promises';
import { ROOT } from './git.mjs';
export const GIT_CONTROL = '/tmp/puppyone-workbench/git';
export function gitControl(format) {
  if (!['sha1', 'sha256'].includes(format)) throw new Error('Invalid workbench object format');
  return `${GIT_CONTROL}/${format}`;
}

export async function initializeGitControl(format) {
  if (process.getuid() !== 0) throw new Error('Git control layout requires the lifecycle controller');
  const directory = gitControl(format);
  await mkdir(directory, { recursive: true, mode: 0o755 });
  const config = `[core]\nrepositoryformatversion=${format === 'sha256' ? 1 : 0}\nbare=false\nfilemode=true\n` +
    (format === 'sha256' ? '[extensions]\nobjectformat=sha256\n' : '') +
    '[user]\nname=Puppyone Agent\nemail=agent@puppyone.invalid\n';
  // Neutral command context, never a stored branch or display pointer. All
  // operations select their actual ref/OID explicitly and check the user HEAD.
  for (const [name, contents] of [['config', config], ['HEAD', 'ref: refs/puppyone-control/unborn\n']]) {
    try { await writeFile(`${directory}/${name}`, contents, { flag: 'wx', mode: 0o444 }); }
    catch (error) {
      if (error.code !== 'EEXIST' || await readFile(`${directory}/${name}`, 'utf8') !== contents) throw error;
    }
  }
  // These are aliases, not another ref database or copies of the worktree. Git
  // commands still run as the unprivileged task, never as this root controller.
  for (const name of ['refs', 'objects', 'packed-refs', 'logs']) {
    const target = `${ROOT}/.git/${name}`;
    try { await symlink(target, `${directory}/${name}`); }
    catch (error) {
      if (error.code !== 'EEXIST' || await readlink(`${directory}/${name}`) !== target) throw error;
    }
  }
  await chmod(directory, 0o555);
  await chmod(GIT_CONTROL, 0o555);
}
