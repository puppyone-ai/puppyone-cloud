import type { ReactNode } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { SWRConfig } from 'swr';
import { expect, it, vi } from 'vitest';
import { useToolsByPath, useProjectTools } from '@/lib/hooks/useData';
import { getToolsByPath, getToolsByProjectId, type Tool } from '@/lib/mcpApi';

vi.mock('@/lib/mcpApi', () => ({ getToolsByPath: vi.fn(), getToolsByProjectId: vi.fn() }));

function isolatedCache() {
  const cache = new Map();
  return function TestCache({ children }: { children: ReactNode }) { return <SWRConfig value={{ provider: () => cache, shouldRetryOnError: false }}>{children}</SWRConfig>; };
}

it('project-scoped path selectors reuse the authorized project query, never the legacy path-only API', async () => {
  vi.mocked(getToolsByProjectId).mockResolvedValueOnce([
    { id: 'same', path: 'table.json' } as Tool, { id: 'other', path: 'other.json' } as Tool,
  ]);
  const hook = renderHook(() => ({ all: useProjectTools('p'), file: useToolsByPath('table.json', 'p') }), { wrapper: isolatedCache() });
  await waitFor(() => expect(hook.result.current.file.tools).toHaveLength(1));
  expect(hook.result.current.file.tools[0].id).toBe('same');
  expect(getToolsByProjectId).toHaveBeenCalledTimes(1); expect(getToolsByPath).not.toHaveBeenCalled();
});

it('does not fetch a disabled tools query and keeps its empty reference stable', () => {
  const hook = renderHook(() => useToolsByPath(undefined), { wrapper: isolatedCache() });
  const initial = hook.result.current.tools;
  hook.rerender();
  expect(hook.result.current.tools).toBe(initial);
  expect(getToolsByPath).not.toHaveBeenCalled();
});

it('keeps the pending fallback stable and then exposes the fetched tools', async () => {
  let resolve!: (tools: Tool[]) => void;
  vi.mocked(getToolsByPath).mockReturnValueOnce(new Promise(done => { resolve = done; }));
  const hook = renderHook(() => useToolsByPath('table.json'), { wrapper: isolatedCache() });
  const initial = hook.result.current.tools;
  hook.rerender();
  expect(hook.result.current.tools).toBe(initial);
  const tools = [{ type: 'search' } as Tool];
  resolve(tools);
  await waitFor(() => expect(hook.result.current.tools).toEqual(tools));
  expect(getToolsByPath).toHaveBeenCalledTimes(1);
});
