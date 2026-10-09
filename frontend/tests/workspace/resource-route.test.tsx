// @vitest-environment node
import { expect, it, vi } from 'vitest';
import WorkspaceResourcePage from '@/app/[workspaceId]/[[...path]]/page';

vi.mock('next/navigation', () => ({ notFound: () => { throw new Error('NOT_FOUND'); } }));
vi.mock('@/features/resource-preview/ResourcePreview', () => ({ ResourcePreview: () => null }));
vi.mock('@/features/workspace/navigation', () => ({ WorkspaceNavigationProvider: () => null }));
const workspaceId = '01926faa-7ed1-7000-a000-000000000001';

it.each(['docs/笔记 #?%.md', 'literal%2F.md', '100%.txt', 'emoji/🐶.md'])(
  'decodes the Next route once for %s', async path => {
    const page = await WorkspaceResourcePage({ params: Promise.resolve({ workspaceId, path: path.split('/').map(encodeURIComponent) }), searchParams: Promise.resolve({}) });
    expect(page.props.children.props.path).toBe(path);
  },
);

it.each([['docs%2Fother'], ['%'], ['%2e%2e'], ['back%5Cslash']])(
  'rejects malformed or separator-injected route segments: %j', async segment => {
    await expect(WorkspaceResourcePage({ params: Promise.resolve({ workspaceId, path: [segment] }), searchParams: Promise.resolve({}) })).rejects.toThrow('NOT_FOUND');
  },
);
