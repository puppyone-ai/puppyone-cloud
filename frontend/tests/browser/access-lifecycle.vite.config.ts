import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../..', import.meta.url));
const ports = fileURLToPath(new URL('./access-lifecycle.ports.ts', import.meta.url));
export default defineConfig({
  root,
  define: { 'process.env.NEXT_PUBLIC_API_URL': JSON.stringify('http://localhost:9090') },
  resolve: { alias: [
    ...['@/contexts/SupabaseAuthProvider', '@/lib/apiClient', '@/lib/workspaceFiles'].map(find => ({ find, replacement: ports })),
    { find: '@', replacement: root },
  ] },
  oxc: { jsx: { runtime: 'automatic' } },
  server: { host: '127.0.0.1', port: 4185, strictPort: true, watch: { ignored: ['**/.next*/**'] } },
});
