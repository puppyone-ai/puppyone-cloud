import { StrictMode } from 'react';
import { SWRConfig } from 'swr';
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ResourcePreview } from '@/features/resource-preview/ResourcePreview';
import type { WorkspaceResource } from '@/features/resource-preview/resourceApi';

const mocks = vi.hoisted(() => ({ read: vi.fn(), replace: vi.fn(), auth: { isAuthReady: true, session: { user: { id: 'user' } } as any, authError: null } }));
vi.mock('@/features/resource-preview/resourceApi', () => ({ readWorkspaceResource: mocks.read }));
vi.mock('@/contexts/SupabaseAuthProvider', () => ({ useAuth: () => mocks.auth }));
vi.mock('@/features/workspace/navigation', () => ({ useWorkspaceRouter: () => ({ replace: mocks.replace }) }));
const id = '019a1111-2222-7333-8444-555555555555';
const entry = { name: 'note.md', path: 'note.md', type: 'markdown', mime_type: 'text/markdown', size_bytes: 10, git_mode: '100644', path_bytes_b64: null };
const resource: WorkspaceResource = { kind: 'file', path: 'note.md', entry, entries: [],
  repository_revision: { tree_oid: 'tree', expected_oid: 'commit', generation: 1 }, next_cursor: null,
  preview: 'text', content_text: '# Hello workspace', content_base64: null, truncated: false };
beforeEach(() => {
  mocks.read.mockReset();
  vi.stubEnv("__NEXT_MANUAL_TRAILING_SLASH", "true");
  mocks.auth.session = { user: { id: 'user' } };
  mocks.auth.isAuthReady = true;
  mocks.read.mockResolvedValue(resource);
  window.history.replaceState({}, '', `/${id}/note.md`);
});

function preview(path = 'note.md') {
  const cache = new Map();
  return render(<StrictMode><SWRConfig value={{ provider: () => cache, dedupingInterval: 5000 }}><ResourcePreview projectId={id} path={path} /></SWRConfig></StrictMode>);
}

it('opens text with one read, no stat/list choreography, even in Strict Mode', async () => {
  preview();
  expect(await screen.findByRole('heading', { name: 'Hello workspace' })).toBeTruthy();
  expect(mocks.read).toHaveBeenCalledTimes(1);
  expect(mocks.read).toHaveBeenCalledWith(id, 'note.md', undefined, undefined);
  expect(screen.getByRole('link', { name: 'Download Puppyone' }).getAttribute('href')).toContain('/download');
});

it('does not read content before sign in and preserves the selected path', async () => {
  mocks.auth.session = null;
  preview('space name.md');
  const link = screen.getByRole('link', { name: 'Sign in' });
  expect(new URL(link.getAttribute('href')!, 'https://web.test').searchParams.get('redirect')).toBe(`/${id}/space%20name.md`);
  expect(mocks.read).not.toHaveBeenCalled();
});

it('renders folder links without fetching each child and pages only on demand', async () => {
  const first = { ...resource, kind: 'directory', path: '', entry: { ...entry, name: '', path: '', type: 'folder' },
    entries: [{ ...entry, name: 'file #?.md', path: 'file #?.md' }, { ...entry, name: 'docs', path: 'docs', type: 'folder' }], next_cursor: 'next' };
  mocks.read.mockResolvedValueOnce(first).mockResolvedValueOnce({ ...first, entries: [{ ...entry, name: 'last.md', path: 'last.md' }], next_cursor: null });
  preview('');
  expect((await screen.findByRole('link', { name: 'file #?.md' })).getAttribute('href')).toBe(`/${id}/file%20%23%3F.md`);
  expect(screen.getByRole('link', { name: 'docs' }).getAttribute('href')).toBe(`/${id}/docs/`);
  expect(mocks.read).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await screen.findByRole('link', { name: 'last.md' });
  expect(mocks.read).toHaveBeenCalledTimes(2);
  expect(mocks.read).toHaveBeenLastCalledWith(id, '', 'next', undefined);
});

it.each([403, 404, 409, 503])('shows an explicit %s failure and retries on demand', async status => {
  mocks.read.mockRejectedValueOnce(Object.assign(new Error('failure'), { status })).mockResolvedValueOnce(resource);
  preview();
  await screen.findByRole('alert');
  expect(screen.queryByText('This folder is empty.')).toBeNull();
  expect(mocks.read).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByRole('heading', { name: 'Hello workspace' });
  expect(mocks.read).toHaveBeenCalledTimes(2);
});

it('copies the resource URL without issuing a share credential', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  preview();
  fireEvent.click(await screen.findByRole('button', { name: 'Copy link' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/${id}/note.md`));
  expect(mocks.read).toHaveBeenCalledTimes(1);
});

it('does not show cached confidential content after switching to an unauthorized account', async () => {
  const cache = new Map();
  const element = () => <SWRConfig value={{ provider: () => cache }}><ResourcePreview projectId={id} path="note.md" /></SWRConfig>;
  const view = render(element());
  await screen.findByRole('heading', { name: 'Hello workspace' });
  mocks.auth.session = { user: { id: 'outsider' } };
  mocks.read.mockRejectedValue(Object.assign(new Error('denied'), { status: 404 }));
  view.rerender(element());
  expect(screen.queryByRole('heading', { name: 'Hello workspace' })).toBeNull();
  await screen.findByRole('alert');
  expect(screen.queryByRole('heading', { name: 'Hello workspace' })).toBeNull();
  expect(mocks.read).toHaveBeenCalledTimes(2);
});

it('renders Markdown as read-only content without executing HTML or loading remote images', async () => {
  mocks.read.mockResolvedValue({ ...resource, content_text: '# Safe\n<script>alert(1)</script>\n![tracking](https://evil.test/pixel)\n[bad](javascript:alert(1))', truncated: true });
  const view = preview();
  await screen.findByRole('heading', { name: 'Safe' });
  expect(view.container.querySelector('script')).toBeNull();
  expect(view.container.querySelector('img')).toBeNull();
  expect(view.container.querySelector('a[href^="javascript:"]')).toBeNull();
  expect(screen.getByText(/This preview is limited/)).toBeTruthy();
});
