/** Build identity covers the shipped executable files, independently of provider. */
import { readdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { join, relative } from 'node:path';
const root = new URL('.', import.meta.url).pathname;
const hash = createHash('sha256');
async function visit(directory) {
  for (const entry of (await readdir(directory, {withFileTypes: true})).sort((a,b) => a.name.localeCompare(b.name, 'en'))) {
    if (entry.name === 'node_modules' || entry.name === 'runtime-manifest.json') continue;
    const file = join(directory, entry.name);
    if (entry.isDirectory()) await visit(file);
    else if (/\.(mjs|py|json)$/.test(entry.name)) {
      hash.update(relative(root, file)); hash.update('\0'); hash.update(await readFile(file)); hash.update('\0');
    }
  }
}
await visit(root);
await writeFile(new URL('./runtime-manifest.json', import.meta.url), JSON.stringify({
  runtime_protocol: 1, file_protocol: 1,
  capabilities: ['workspace_lifecycle', 'native_files', 'file_watch'], build_id: hash.digest('hex')
}) + '\n');
