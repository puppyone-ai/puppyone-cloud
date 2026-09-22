import { StrictMode, type ReactNode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useStructuredNodeData } from '@/features/files/hooks/useStructuredNodeData';
import type { Tool } from '@/lib/mcpApi';
import type { NodeInfo } from '@/lib/contentTreeApi';

const query = vi.hoisted(() => ({
  loading: false,
  tools: undefined as Tool[] | undefined,
  error: undefined as Error | undefined,
  refresh: vi.fn(),
}));

vi.mock('@/lib/hooks/useData', () => ({
  // Deliberately return a fresh fallback. Consumers must also reset idempotently.
  useToolsByPath: () => ({ tools: query.tools ?? [], isLoading: query.loading, error: query.error }),
  useTable: () => ({ tableData: undefined, refresh: query.refresh }),
  refreshToolsByPath: vi.fn(),
  refreshProjectTools: vi.fn(),
}));
vi.mock('@/lib/mcpApi', () => ({ createTool: vi.fn(), deleteTool: vi.fn() }));

const nodes: NodeInfo[] = [];
const defaults = { projectId: 'project-a', activeNodeId: 'note.md', activeNodeType: 'markdown', activeFormatDefaultViewer: 'markdown', contentNodes: nodes };
const wrapper = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>;

function mount(initialProps = defaults) {
  let renders = 0;
  const hook = renderHook((props) => {
    if (++renders > 30) throw new Error('Workspace effects did not converge');
    return useStructuredNodeData(props);
  }, { wrapper, initialProps });
  return { ...hook, renders: () => renders };
}

beforeEach(() => { query.loading = false; query.tools = undefined; query.error = undefined; });

describe('structured node state convergence', () => {
  it.each(['disabled', 'loading', 'error', 'empty'])('settles for %s tools queries', (mode) => {
    query.loading = mode === 'loading';
    query.error = mode === 'error' ? new Error('unavailable') : undefined;
    query.tools = mode === 'empty' ? [] : undefined;
    const props = mode === 'disabled' ? defaults : { ...defaults, activeNodeType: 'json', activeFormatDefaultViewer: 'json-table' };
    const hook = mount(props);
    expect(hook.result.current.accessPoints).toEqual([]);
    expect(hook.renders()).toBeLessThan(30);
  });

  it('clears old access points when a non-structured file is selected', () => {
    query.tools = [{ type: 'search', json_path: '/items' } as Tool];
    const hook = mount({ ...defaults, activeNodeType: 'json', activeFormatDefaultViewer: 'json-table' });
    expect(hook.result.current.accessPoints).toHaveLength(1);
    hook.rerender(defaults);
    expect(hook.result.current.accessPoints).toEqual([]);
  });

  it('initializes the same file path independently for each project', () => {
    const props = { ...defaults, activeNodeId: 'table.json', activeNodeType: 'json', activeFormatDefaultViewer: 'json-table' };
    query.tools = [{ type: 'search', json_path: '/a' } as Tool];
    const hook = mount(props);
    query.tools = [{ type: 'search', json_path: '/b' } as Tool];
    hook.rerender({ ...props, projectId: 'project-b' });
    expect(hook.result.current.accessPoints.map(p => p.path)).toEqual(['/b']);
  });

  it('does not overwrite local access-point edits on an unrelated render', () => {
    const props = { ...defaults, activeNodeType: 'json', activeFormatDefaultViewer: 'json-table' };
    query.tools = [];
    const hook = mount(props);
    const edited = [{ id: 'draft', path: '/draft', permissions: { search: true } }];
    act(() => hook.result.current.setAccessPoints(edited));
    hook.rerender({ ...props });
    expect(hook.result.current.accessPoints).toEqual(edited);
  });

  it('initializes permissions after the same query recovers from an error', () => {
    const props = { ...defaults, activeNodeType: 'json', activeFormatDefaultViewer: 'json-table' };
    query.error = new Error('temporarily unavailable');
    const hook = mount(props);
    query.error = undefined;
    query.tools = [{ type: 'search', json_path: '/recovered' } as Tool];
    hook.rerender({ ...props });
    expect(hook.result.current.accessPoints.map(p => p.path)).toEqual(['/recovered']);
  });
});
