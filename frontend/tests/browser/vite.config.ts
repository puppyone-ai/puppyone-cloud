import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

// A local browser fixture, outside Next's routes and production bundle.
export default defineConfig({
  root: fileURLToPath(new URL('../..', import.meta.url)),
  resolve: { alias: { '@': fileURLToPath(new URL('../..', import.meta.url)) } },
  oxc: { jsx: { runtime: 'automatic' } },
  server: { host: '127.0.0.1', port: 4174, strictPort: true },
});
