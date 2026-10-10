import type { ReactNode } from 'react';
import type { WorkspaceResource } from '@/features/resource-preview/resourceApi';

export const projectId = '11111111-1111-4111-8111-111111111111';
export const requests: string[] = [];
export let navigate = (_path: string) => {};
export function installNavigation(callback: typeof navigate) { navigate = callback; }
export function useAuth() { return { isAuthReady: true, session: { user: { id: 'preview-fixture' } }, authError: null }; }
const router = { replace: (url: string) => window.history.replaceState({}, '', url) };
export function useWorkspaceRouter() { return router; }
export default function Link({ href, children, className, 'aria-current': current }: { href: string; children: ReactNode; className?: string; prefetch?: boolean; 'aria-current'?: 'page' }) {
  return <a href={href} className={className} aria-current={current} onClick={event => {
    if (!href.startsWith(`/${projectId}`) || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault(); window.history.pushState({}, '', href);
    navigate(decodeURIComponent(href.slice(projectId.length + 2)).replace(/\/$/, ''));
  }}>{children}</a>;
}
const file = (name: string, type = 'markdown') => ({ name, path: name, type, mime_type: 'text/markdown', size_bytes: 120, git_mode: '100644', path_bytes_b64: null });
const base: WorkspaceResource = { kind: 'file', path: '', entry: file(''), entries: [], next_cursor: null,
  repository_revision: { tree_oid: 'fixture-tree', expected_oid: 'fixture-commit', generation: 1 },
  preview: 'text', content_text: '', content_base64: null, truncated: false };
export async function readWorkspaceResource(_project: string, path: string): Promise<WorkspaceResource> {
  requests.push(path);
  if (!path) return { ...base, kind: 'directory', entry: file('', 'folder'), entries: [file('.DS_Store'), file('Research', 'folder'), file('Welcome.md'), file('Meeting notes.md'), ...Array.from({ length: 25 }, (_, index) => file(`Document ${index + 1}.md`))] };
  return { ...base, path, entry: file(path), content_text: path === 'Welcome.md' ? '# Research workspace\n\nA shared place for ideas, notes, and the work ahead.\n\n## Start with the questions\n\nKeep source material together and make room for discussion. Everyone with workspace access can preview these documents.\n\n- Gather research and references\n- Connect ideas across documents\n- Work with your team in Puppyone\n\n> Good work starts with a shared understanding.\n\n## Next steps\n\nOpen another file from the sidebar, or download Puppyone to work with the complete workspace.' : '# Meeting notes\n\nDecisions and next steps from our latest discussion.' };
}
