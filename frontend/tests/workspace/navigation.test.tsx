import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { useSyncExternalStore, type ReactNode } from 'react';
import { WorkspaceNavigationProvider, WorkspaceLink, filesHref, gitHref, parseWorkspaceLocation, returnToFiles, useWorkspaceRouter } from '@/features/workspace/navigation';
import { ProjectSessionProvider, useSessionValue } from '@/features/workspace/session';
import { ResponsiveWorkspaceProvider } from '@/features/workspace/responsive';
import { ProjectsHeader, ProjectHeaderBreadcrumbs } from '@/components/ProjectsHeader';
import { useEditorSaveGuards } from '@/features/files/hooks/useEditorSaveGuards';
import { useDataRouteController } from '@/features/files/hooks/useDataRouteController';

const route = vi.hoisted(() => ({ href: '/projects/p/data', listeners: new Set<() => void>(), push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: route.push, replace: route.replace, prefetch: route.prefetch }),
  usePathname: () => useSyncExternalStore(cb => { route.listeners.add(cb); return () => { route.listeners.delete(cb); }; }, () => route.href.split('?')[0]),
  useSearchParams: () => new URLSearchParams(route.href.split('?')[1]),
}));
vi.mock('@/features/files/hooks/usePathResolver', () => ({ usePathResolver: (_project: string, path: string[]) => ({ activeNodeId: path.join('/'), textContent: '' }) }));

beforeEach(() => {
  route.href = '/projects/p/data'; route.push.mockClear(); route.replace.mockClear();
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
function commit(href: string) { act(() => { route.href = href; route.listeners.forEach(fn => fn()); }); }
function Wrapper({ children }: { children: ReactNode }) {
  return <WorkspaceNavigationProvider><ResponsiveWorkspaceProvider><ProjectSessionProvider projectId='p'>{children}</ProjectSessionProvider></ResponsiveWorkspaceProvider></WorkspaceNavigationProvider>;
}
function Guard() { useEditorSaveGuards({ dirty: true, save: async () => {} }); return null; }

it('encodes paths once, preserves deep links, and refuses another project bookmark', () => {
  const href = filesHref('p', ['合同', '100%.md'], 'markdown');
  const [path, search] = href.split('?');
  expect(parseWorkspaceLocation(path, search)?.path).toEqual(['合同', '100%.md']);
  expect(returnToFiles('q', href)).toBe('/projects/q/data');
  expect(gitHref('p')).toBe('/projects/p/changes');
});

it('never presents a requested Git view as the committed Files view; reverse click is issued', () => {
  render(<ProjectsHeader projectId='p' activeView='files' pathSegments={[{ label: 'Project' }]} />, { wrapper: Wrapper });
  fireEvent.click(screen.getByRole('link', { name: 'Git' }));
  expect(route.push).toHaveBeenLastCalledWith('/projects/p/changes', { scroll: false });
  expect(screen.getByRole('link', { name: 'Files' }).getAttribute('aria-current')).toBe('page');
  expect(screen.getByRole('link', { name: 'Git' }).className).not.toContain('bg-[var(--po-selected)]');
  fireEvent.click(screen.getByRole('link', { name: 'Files' }));
  expect(route.push).toHaveBeenLastCalledWith('/projects/p/data', { scroll: false });
});

it('confirms a breadcrumb exactly once, and cancellation also protects command-style project navigation', () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  function ProjectButton() { const router = useWorkspaceRouter(); return <button onClick={() => router.push('/projects/q/data')}>Other project</button>; }
  render(<><Guard /><ProjectHeaderBreadcrumbs pathSegments={[{ label: 'Project', href: '/projects/p/data/root' }, { label: 'note.md' }]} /><ProjectButton /></>, { wrapper: Wrapper });
  fireEvent.click(screen.getByRole('link', { name: 'Project' }));
  expect(confirm).toHaveBeenCalledOnce();
  expect(route.push).toHaveBeenCalledOnce();
  confirm.mockReturnValue(false);
  fireEvent.click(screen.getByRole('button', { name: 'Other project' }));
  expect(confirm).toHaveBeenCalledTimes(2);
  expect(route.push).toHaveBeenCalledOnce();
});

it('modifier clicks do not run the current-tab leave guard', () => {
  const confirm = vi.spyOn(window, 'confirm');
  render(<><Guard /><WorkspaceLink href='/projects/p/changes' onClick={event => { if (event.metaKey) event.preventDefault(); }}>Git</WorkspaceLink></>, { wrapper: Wrapper });
  fireEvent.click(screen.getByRole('link'), { metaKey: true });
  expect(confirm).not.toHaveBeenCalled(); expect(route.push).not.toHaveBeenCalled();
});

it('aggregates multiple dirty editors into one leave confirmation', () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  render(<><Guard /><Guard /><WorkspaceLink href='/projects/q/data'>Other project</WorkspaceLink></>, { wrapper: Wrapper });
  fireEvent.click(screen.getByRole('link'));
  expect(confirm).toHaveBeenCalledOnce();
  expect(route.push).not.toHaveBeenCalled();
});

it('restores the actual Files URL on mount and subsequent history commits without mirrored route params', () => {
  route.href = '/projects/p/data/folder-b?type=folder';
  const hook = renderHook(() => ({ ...useDataRouteController({ projectId: 'p' }), remembered: useSessionValue('filesHref')[0] }), { wrapper: Wrapper });
  expect(hook.result.current.path).toEqual(['folder-b']);
  expect(hook.result.current.remembered).toBe(route.href);
  commit('/projects/p/data/folder-a/note.md');
  expect(hook.result.current.path).toEqual(['folder-a', 'note.md']);
  expect(hook.result.current.remembered).toBe(route.href);
});

it('ignores a creation callback whose originating location is no longer current', () => {
  const hook = renderHook(() => useDataRouteController({ projectId: 'p' }), { wrapper: Wrapper });
  const oldNavigate = hook.result.current.navigateTo;
  commit('/projects/p/data/other.md');
  act(() => { expect(oldNavigate(['created.md'])).toBe(false); });
  expect(route.push).not.toHaveBeenCalled();
});
