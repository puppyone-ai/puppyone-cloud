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
const file = (name: string, type = 'markdown') => ({ name: name.split('/').pop()!, path: name, type, mime_type: 'text/markdown', size_bytes: 120, git_mode: '100644', path_bytes_b64: null });
const base: WorkspaceResource = { kind: 'file', path: '', entry: file(''), entries: [], next_cursor: null,
  repository_revision: { tree_oid: 'fixture-tree', expected_oid: 'fixture-commit', generation: 1 },
  preview: 'text', content_text: '', content_base64: null, truncated: false };
export async function readWorkspaceResource(_project: string, path: string): Promise<WorkspaceResource> {
  requests.push(path);
  if (path === 'Research') return { ...base, kind: 'directory', path, entry: file(path, 'folder'), entries: [{ ...file('Plan.md'), path: 'Research/Plan.md' }] };
  if (!path) return { ...base, kind: 'directory', entry: file('', 'folder'), entries: [file('.DS_Store'), file('Research', 'folder'), file('Welcome.md'), file('Meeting notes.md'), ...Array.from({ length: 25 }, (_, index) => file(`Document ${index + 1}.md`))] };
  return { ...base, path, entry: file(path), content_text: path === 'Welcome.md' ? '# Research workspace\n\nA shared place for ideas, notes, and the work ahead.\n\n## Start with the questions\n\nKeep source material together and make room for discussion. Everyone with workspace access can preview these documents.\n\n- Gather research and references\n- Connect ideas across documents\n- Work with your team in Puppyone\n\n> Good work starts with a shared understanding.\n\n## Next steps\n\nOpen another file from the sidebar, or download Puppyone to work with the complete workspace.' : '# Meeting notes\n\nDecisions and next steps from our latest discussion.' };
}

// Reference surface uses the actual signed-in Workspace components.
export function useRouter() { return { ...router, prefetch() {} }; }
export function usePendingNavigation() { return null; }
export function useNavigationGuard() {}
export function returnToFiles(id: string) { return `/projects/${id}/data`; }
export function parseWorkspaceLocation() { return null; }
const referenceEntries = [file('Research', 'folder'), file('Welcome.md'), file('Meeting notes.md'), ...Array.from({ length: 25 }, (_, i) => file(`Document ${i + 1}.md`))];
const referenceNodes = referenceEntries.map(entry => ({ ...entry, id: entry.path }));
export function useExplorerRootNodes() { return { rootNodes: referenceNodes, isLoading: false, error: null, refresh: async () => {} }; }
export function useExplorerTreeDir() { return { nodes: [], isLoading: false, isValidating: false, error: null, refresh: async () => {} }; }
export function usePathname() { return window.location.pathname; }
export function useSearchParams() { return new URLSearchParams(window.location.search); }
