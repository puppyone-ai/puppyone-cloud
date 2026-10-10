import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../..', import.meta.url));
const ports = fileURLToPath(new URL('./resource-preview.ports.tsx', import.meta.url));
export default defineConfig({
  root,
  define: { 'process.env.NEXT_PUBLIC_API_URL': JSON.stringify('https://cloud-fixture.puppyone.ai') },
  resolve: { alias: [
    { find: 'next/link', replacement: ports },
    { find: 'next/navigation', replacement: ports },
    { find: '@/lib/hooks/useData', replacement: ports },
    { find: '@/contexts/SupabaseAuthProvider', replacement: ports },
    { find: '@/features/workspace/navigation', replacement: ports },
    { find: '@/lib/projectsApi', replacement: ports },
    { find: '@', replacement: root },
  ] },
  plugins: [{ name: 'resource-preview-fixture', enforce: 'pre', resolveId(source, importer) {
    if (source === './resourceApi' && importer?.endsWith('/resourceQueries.ts')) return ports;
  } }],
  oxc: { jsx: { runtime: 'automatic' } },
  server: { host: '127.0.0.1', port: 4175, strictPort: true, watch: { ignored: ['**/.next*/**'] } },
});
