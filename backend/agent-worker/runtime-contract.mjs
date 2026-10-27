import { readFileSync } from 'node:fs';
export const runtimeManifest = Object.freeze(JSON.parse(readFileSync(new URL('./runtime-manifest.json', import.meta.url), 'utf8')));
