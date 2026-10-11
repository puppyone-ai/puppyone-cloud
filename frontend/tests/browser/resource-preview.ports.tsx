import type { ReactNode, ComponentProps } from 'react';
import type { WorkspaceResource } from '@/features/resource-preview/resourceApi';

export const projectId = '11111111-1111-4111-8111-111111111111';
export const requests: string[] = [];
export let navigate = (_path: string) => {};
export function installNavigation(callback: typeof navigate) { navigate = callback; }
export function useAuth() { return { isAuthReady: true, session: { user: { id: 'preview-fixture' } }, authError: null }; }
const router = { replace: (url: string) => window.history.replaceState({}, '', url) };
export function useWorkspaceRouter() { return router; }
export const getProject = async () => ({ id: projectId, name: 'Research workspace' });
export { Link as WorkspaceLink };
export default function Link({ href, children, prefetch: _prefetch, onClick, ...props }: ComponentProps<'a'> & { href: string; prefetch?: boolean }) {
  return <a {...props} href={href} onClick={event => {
    onClick?.(event);
    if (!href.startsWith(`/${projectId}`) || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); window.history.pushState({}, '', href);
    navigate(decodeURIComponent(href.slice(projectId.length + 2)).replace(/\/$/, ''));
  }}>{children}</a>;
}
const file = (name: string, type = /\.md$/i.test(name) ? 'markdown' : 'file') => ({ name: name.split('/').pop()!, path: name, type, mime_type: /\.md$/i.test(name) ? 'text/markdown' : 'text/plain', size_bytes: 120, git_mode: '100644', path_bytes_b64: null });
const base: WorkspaceResource = { kind: 'file', path: '', entry: file(''), entries: [], next_cursor: null,
  repository_revision: { tree_oid: 'fixture-tree', expected_oid: 'fixture-commit', generation: 1 },
  preview: 'text', content_text: '', content_base64: null, truncated: false };
export async function readWorkspaceResource(_project: string, path: string): Promise<WorkspaceResource> {
  requests.push(path);
  if (path === 'Research') return { ...base, kind: 'directory', path, entry: file(path, 'folder'), entries: [{ ...file('Plan.md'), path: 'Research/Plan.md' }] };
  if (!path) return { ...base, kind: 'directory', entry: file('', 'folder'), entries: [file('.DS_Store'), file('Research', 'folder'), file('Welcome.md'), file('Meeting notes.md'), file('Metrics.csv'), file('Settings.json'), ...Array.from({ length: 25 }, (_, index) => file(`Document ${index + 1}.md`))] };
  if (path === 'Article.md') return { ...base, path, entry: file(path), content_text: '# Shared article\n\nIdentical typography and image rendering in Share and Workspace.\n\n![Blue diagram](data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22%20width=%22320%22%20height=%22120%22%3E%3Crect%20width=%22320%22%20height=%22120%22%20rx=%2216%22%20fill=%22%233b82f6%22/%3E%3C/svg%3E)\n\n## Notes\n\n- Same renderer\n- Read-only access' };
  if (path === 'Page.html') return { ...base, path, entry: file(path), content_text: '<!doctype html><html><head><style>body{margin:0;padding:48px;font:16px system-ui;background:#f4f7fb;color:#172554}.card{padding:32px;border-radius:16px;background:white;border:1px solid #bfdbfe}h1{font-size:32px;color:#2563eb}</style></head><body><div class="card"><h1>Shared HTML</h1><p>The same document in Share and Workspace.</p></div><script>document.body.textContent="UNSAFE SCRIPT RAN"</script></body></html>' };
  if (path === 'Metrics.csv') return { ...base, path, entry: file(path, 'file'), content_text: 'Name,Count,Status\nResearch,42,Active\nDesign,18,Review\nWriting,7,Done' };
  if (path === 'Settings.json') return { ...base, path, entry: file(path, 'file'), content_text: '{\n  "name": "Research workspace",\n  "shared": true,\n  "items": [42, 18, 7]\n}' };
  return { ...base, path, entry: file(path), content_text: path === 'Welcome.md' ? '# Research workspace\n\nA shared place for ideas, notes, and the work ahead.\n\n## Start with the questions\n\nKeep source material together and make room for discussion. Everyone with workspace access can preview these documents.\n\n- Gather research and references\n- Connect ideas across documents\n- Work with your team in Puppyone\n\n> Good work starts with a shared understanding.\n\n## Next steps\n\nOpen another file from the sidebar, or download Puppyone to work with the complete workspace.' : '# Meeting notes\n\nDecisions and next steps from our latest discussion.' };
}

// Reference surface uses the actual signed-in Workspace components.
export function useRouter() { return { ...router, prefetch() {} }; }
export function usePendingNavigation() { return null; }
export function useNavigationGuard() {}
export function returnToFiles(id: string) { return `/projects/${id}/data`; }
export function parseWorkspaceLocation() { return null; }
const referenceEntries = [file('Research', 'folder'), file('Welcome.md'), file('Meeting notes.md'), file('Metrics.csv'), file('Settings.json'), ...Array.from({ length: 25 }, (_, i) => file(`Document ${i + 1}.md`))];
const referenceNodes = referenceEntries.map(entry => ({ ...entry, id: entry.path }));
export function useExplorerRootNodes() { return { rootNodes: referenceNodes, isLoading: false, error: null, refresh: async () => {} }; }
export function useExplorerTreeDir() { return { nodes: [], isLoading: false, isValidating: false, error: null, refresh: async () => {} }; }
export function usePathname() { return window.location.pathname; }
export function useSearchParams() { return new URLSearchParams(window.location.search); }
