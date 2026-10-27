import { expect, it, vi } from 'vitest';
import { readWorkspaceResource } from '@/features/resource-preview/resourceApi';

const api = vi.hoisted(() => ({ get: vi.fn().mockResolvedValue({}) }));
vi.mock('@/lib/apiClient', () => api);

it('requests complete authorized text and keeps paths/query characters lossless', async () => {
  await readWorkspaceResource('project', 'docs/name #?%.md');
  const [url, options] = api.get.mock.calls[0];
  const params = new URL(url, 'https://app.test').searchParams;
  expect(params.get('path')).toBe('docs/name #?%.md');
  expect(params.get('text_mode')).toBe('full');
  expect(options.cache).toBe('no-store');
});

it('sends byte locators independently of display names and preserves paging', async () => {
  await readWorkspaceResource('project', 'display-only', 'next', 'bGVnYWN5Lf8=');
  const params = new URL(api.get.mock.calls[0][0], 'https://app.test').searchParams;
  expect(params.has('path')).toBe(false);
  expect(params.get('path_bytes_b64')).toBe('bGVnYWN5Lf8=');
  expect(params.get('cursor')).toBe('next');
});
