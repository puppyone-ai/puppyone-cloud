import { StrictMode } from 'react';
import { SWRConfig } from 'swr';
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ResourcePreview } from '@/features/resource-preview/ResourcePreview';
import type { WorkspaceResource } from '@/features/resource-preview/resourceApi';

const mocks = vi.hoisted(() => ({ read: vi.fn(), project: vi.fn(), replace: vi.fn(), auth: { isAuthReady: true, session: { user: { id: 'user' } } as any, authError: null } }));
vi.mock('@/features/resource-preview/resourceApi', () => ({ readWorkspaceResource: mocks.read }));
vi.mock('@/contexts/SupabaseAuthProvider', () => ({ useAuth: () => mocks.auth }));
vi.mock('@/features/workspace/navigation', async () => ({ WorkspaceLink: (await import('next/link')).default, useWorkspaceRouter: () => ({ replace: mocks.replace }) }));
vi.mock('@/lib/projectsApi', () => ({ getProject: mocks.project }));
const id = '019a1111-2222-7333-8444-555555555555';
const entry = { name: 'note.md', path: 'note.md', type: 'markdown', mime_type: 'text/markdown', size_bytes: 10, git_mode: '100644', path_bytes_b64: null };
const resource: WorkspaceResource = { kind: 'file', path: 'note.md', entry, entries: [],
  repository_revision: { tree_oid: 'tree', expected_oid: 'commit', generation: 1 }, next_cursor: null,
  preview: 'text', content_text: '# Hello workspace', content_base64: null, truncated: false };
const rootDirectory: WorkspaceResource = { ...resource, kind: 'directory', path: '', entry: { ...entry, name: '', path: '', type: 'folder' }, entries: [entry], preview: 'unavailable' };
beforeEach(() => {
  mocks.read.mockReset();
  mocks.project.mockResolvedValue({ id, name: 'Research workspace' });
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 0, 1280, 800));
  Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
  Range.prototype.getBoundingClientRect = () => new DOMRect();

  vi.stubEnv("__NEXT_MANUAL_TRAILING_SLASH", "true");
  mocks.auth.session = { user: { id: 'user' } };
  mocks.auth.isAuthReady = true;
  mocks.read.mockImplementation((_id, path) => Promise.resolve(path ? resource : rootDirectory));
  window.history.replaceState({}, '', `/${id}/note.md`);
});

function preview(path = 'note.md') {
  const cache = new Map();
  return render(<StrictMode><SWRConfig value={{ provider: () => cache, dedupingInterval: 5000 }}><ResourcePreview projectId={id} path={path} /></SWRConfig></StrictMode>);
}

it('reads a deep-linked file once and lists its tree in parallel, even in Strict Mode', async () => {
  preview();
  expect(await screen.findByText(/Hello workspace/)).toBeTruthy();
  expect(mocks.read).toHaveBeenCalledTimes(2);
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
    entries: [{ ...entry, name: 'file #?.bin', path: 'file #?.bin' }, { ...entry, name: 'docs', path: 'docs', type: 'folder' }], next_cursor: 'next' };
  mocks.read.mockResolvedValueOnce(first).mockResolvedValueOnce({ ...first, entries: [{ ...entry, name: 'last.bin', path: 'last.bin' }], next_cursor: null });
  preview('');
  expect((await screen.findByRole('link', { name: 'file #?.bin' })).getAttribute('href')).toBe(`/${id}/file%20%23%3F.bin`);
  expect(screen.getByRole('link', { name: 'docs' }).getAttribute('href')).toBe(`/${id}/docs/`);
  expect(mocks.read).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await screen.findByRole('link', { name: 'last.bin' });
  expect(mocks.read).toHaveBeenCalledTimes(2);
  expect(mocks.read).toHaveBeenLastCalledWith(id, '', 'next', undefined);
});

it.each([403, 404, 409, 503])('shows an explicit %s failure and retries on demand', async status => {
  let failed = false;
  mocks.read.mockImplementation((_id, path) => {
    if (!path) return Promise.resolve(rootDirectory);
    if (!failed) { failed = true; return Promise.reject(Object.assign(new Error('failure'), { status })); }
    return Promise.resolve(resource);
  });
  preview();
  await screen.findByRole('alert');
  expect(screen.queryByText('This folder is empty.')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByText(/Hello workspace/);
  expect(mocks.read.mock.calls.filter(call => call[1] === 'note.md')).toHaveLength(2);
});

it('copies the resource URL without issuing a share credential', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  preview();
  fireEvent.click(await screen.findByRole('button', { name: 'Copy link' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/${id}/note.md`));
  expect(mocks.read).toHaveBeenCalledTimes(2);
});

it('does not show cached confidential content after switching to an unauthorized account', async () => {
  const cache = new Map();
  const element = () => <SWRConfig value={{ provider: () => cache }}><ResourcePreview projectId={id} path="note.md" /></SWRConfig>;
  const view = render(element());
  await screen.findByText(/Hello workspace/);
  mocks.auth.session = { user: { id: 'outsider' } };
  mocks.read.mockRejectedValue(Object.assign(new Error('denied'), { status: 404 }));
  view.rerender(element());
  expect(screen.queryByText(/Hello workspace/)).toBeNull();
  await screen.findAllByRole('alert');
  expect(screen.queryByText(/Hello workspace/)).toBeNull();
  expect(mocks.read).toHaveBeenCalledTimes(4);
});

it('renders Markdown as read-only content with Workspace image rendering while keeping scripts disabled', async () => {
  mocks.read.mockImplementation((_id, path) => Promise.resolve(!path ? rootDirectory : { ...resource, content_text: '# Safe\n<script>alert(1)</script>\n![tracking](https://evil.test/pixel)\n[bad](javascript:alert(1))', truncated: true }));
  const view = preview();
  await screen.findByText(/Safe/);
  expect(view.container.querySelector('script')).toBeNull();
  expect(view.container.querySelector('img[src]')?.getAttribute('src')).toBe('https://evil.test/pixel');
  expect(view.container.querySelector('a[href^="javascript:"]')).toBeNull();
  expect(screen.getByText(/This preview is limited/)).toBeTruthy();
});


it('previews only one readable document and reuses it when its canonical URL is opened', async () => {
  const directory = { ...resource, kind: 'directory' as const, path: '',
    entry: { ...entry, name: '', path: '', type: 'folder' },
    entries: [{ ...entry, name: '.DS_Store', path: '.DS_Store' }, entry,
      ...Array.from({ length: 30 }, (_, index) => ({ ...entry, name: `other-${index}.md`, path: `other-${index}.md` }))] };
  mocks.read.mockImplementation((_id, path) => Promise.resolve(path === '' ? directory : resource));
  const cache = new Map();
  const element = (path: string) => <StrictMode><SWRConfig value={{ provider: () => cache, dedupingInterval: 5000 }}><ResourcePreview projectId={id} path={path} /></SWRConfig></StrictMode>;
  const view = render(element(''));
  await screen.findByText(/Hello workspace/);
  expect(screen.queryByRole('link', { name: '.DS_Store' })).toBeNull();
  expect(mocks.read).toHaveBeenCalledTimes(2);
  expect(screen.getByRole('link', { name: 'note.md' }).getAttribute('aria-current')).toBe('page');
  view.rerender(element('note.md'));
  await screen.findByText(/Hello workspace/);
  expect(screen.getByRole('link', { name: 'other-29.md' })).toBeTruthy();
  expect(mocks.read).toHaveBeenCalledTimes(2);
  view.unmount();
  render(element('note.md'));
  await screen.findByText(/Hello workspace/);
  expect(screen.getByRole('link', { name: 'other-29.md' })).toBeTruthy();
  expect(mocks.read).toHaveBeenCalledTimes(2);
});

it('expands only the requested directory and reuses it on collapse/reopen', async () => {
  const folder = { ...entry, type: 'folder', path: 'docs', name: 'docs' };
  const child = { ...entry, path: 'docs/note.md' };
  mocks.read.mockImplementation(async (_id, path) => {
    if (path === '') return { ...rootDirectory, entries: [folder, entry] };
    if (path === 'docs') return { ...rootDirectory, path, entry: folder, entries: [child] };
    return resource;
  });
  preview();
  await screen.findByText(/Hello workspace/);
  expect(mocks.read.mock.calls.map(call => call[1])).not.toContain('docs');
  fireEvent.click(screen.getByRole('button', { name: 'Expand docs' }));
  await waitFor(() => expect(screen.getAllByRole('link', { name: 'note.md' })).toHaveLength(2));
  fireEvent.click(screen.getByRole('button', { name: 'Collapse docs' }));
  fireEvent.click(screen.getByRole('button', { name: 'Expand docs' }));
  await waitFor(() => expect(screen.getAllByRole('link', { name: 'note.md' })).toHaveLength(2));
  expect(mocks.read.mock.calls.filter(call => call[1] === 'docs')).toHaveLength(1);
  expect(mocks.read.mock.calls.filter(call => call[1] === 'docs/note.md')).toHaveLength(0);
});

it('keeps the preview read-only in Live and Source modes and exposes no writes', async () => {
  const view = preview();
  await screen.findByText(/Hello workspace/);
  expect(view.container.querySelector('[data-readonly="true"]')).not.toBeNull();
  expect(view.container.querySelector('[contenteditable="true"]')).toBeNull();
  fireEvent.click(screen.getByTitle('View mode: Live view'));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Source code' }));
  expect(view.container.querySelector('[data-live-preview="false"][data-readonly="true"]')).not.toBeNull();
  expect(screen.queryByRole('button', { name: /Save|Create|Upload/ })).toBeNull();
  expect(mocks.read).toHaveBeenCalledTimes(2);
});

it('does not let a slow file response replace a newly selected file', async () => {
  let finishOld!: (value: WorkspaceResource) => void;
  mocks.read.mockImplementation((_id, path) => path === 'old.md' ? new Promise(resolve => { finishOld = resolve; }) :
    Promise.resolve(path === '' ? rootDirectory : { ...resource, path, entry: { ...entry, path, name: path }, content_text: '# New document' }));
  const cache = new Map();
  const element = (path: string) => <SWRConfig value={{ provider: () => cache }}><ResourcePreview projectId={id} path={path} /></SWRConfig>;
  const view = render(element('old.md'));
  view.rerender(element('new.md'));
  await screen.findByText(/New document/);
  finishOld({ ...resource, path: 'old.md', content_text: '# Obsolete document' });
  await waitFor(() => expect(screen.queryByText(/Obsolete document/)).toBeNull());
});

it('keeps empty folders and unsupported files within the Workspace shell', async () => {
  mocks.read.mockResolvedValue({ ...rootDirectory, entries: [] });
  const view = preview('');
  await screen.findByText('This folder is empty');
  expect(screen.queryByRole('button', { name: 'New Markdown' })).toBeNull();
  expect(view.container.querySelector('[data-workspace-header]')).not.toBeNull();
});


it('deduplicates the server byte locator and the same Unicode file URL', async () => {
  const byteEntry = { ...entry, path_bytes_b64: btoa(entry.path) };
  mocks.read.mockImplementation(async (_id, path) => path === ''
    ? { ...rootDirectory, entries: [byteEntry] }
    : { ...resource, entry: byteEntry });
  const cache = new Map();
  const element = (path: string) => <SWRConfig value={{ provider: () => cache, dedupingInterval: 5000 }}><ResourcePreview projectId={id} path={path} /></SWRConfig>;
  const view = render(element(''));
  await screen.findByText(/Hello workspace/);
  view.rerender(element('note.md'));
  await screen.findByText(/Hello workspace/);
  expect(mocks.read).toHaveBeenCalledTimes(2);
});


it('switches a shared CSV between native table and source without another resource read', async () => {
  const csv = { ...resource, path: 'data.csv', entry: { ...entry, name: 'data.csv', path: 'data.csv', type: 'file', mime_type: 'text/csv' }, content_text: 'Name,Count\nAlice,42' };
  mocks.read.mockImplementation((_id, path) => Promise.resolve(path ? csv : { ...rootDirectory, entries: [csv.entry] }));
  preview('data.csv');
  await screen.findByRole('grid');
  fireEvent.click(screen.getByTitle('View mode: Preview table'));
  expect(screen.queryByRole('menuitem', { name: 'Edit table' })).toBeNull();
  fireEvent.click(screen.getByRole('menuitem', { name: 'Source' }));
  fireEvent.click(screen.getByTitle('View mode: Source'));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Preview table' }));
  await screen.findByRole('grid');
  expect(mocks.read).toHaveBeenCalledTimes(2);
  expect(mocks.read.mock.calls.filter(call => call[1] === 'data.csv')).toHaveLength(1);
});
