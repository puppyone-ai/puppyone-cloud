import { ResponsiveWorkspaceProvider } from '@/features/workspace/responsive';
import { act, fireEvent, render as renderUI, renderHook, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { WorkspaceProjectRail } from '@/components/sidebar/WorkspaceProjectRail';
import { DirectoryLoadingState } from '@/components/loading/DirectoryLoadingState';
import { ExplorerSessionProvider, ExplorerSessionsProvider, useExplorerActions, useIsExpanded } from '@/features/files/explorerSession';
import { WorkspaceNavigationProvider } from '@/features/workspace/navigation';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), prefetch: vi.fn() }), usePathname: () => '/home', useSearchParams: () => new URLSearchParams() }));
vi.mock('@/components/UserMenuPanel', () => ({ default: () => null }));

it('rail has a placeholder before a successful snapshot, and empty only after success', () => {
  const view = render(<WorkspaceProjectRail projects={[]} userInitial='' projectsLoading />);
  expect(screen.getByLabelText('Projects').getAttribute('aria-busy')).toBe('true');
  expect(screen.queryByText('No projects yet')).toBeNull();
  expect(screen.getByTitle('Create new project').hasAttribute('disabled')).toBe(true);
  view.rerender(<WorkspaceProjectRail projects={[]} userInitial='U' />);
  expect(screen.getByText('No projects yet')).toBeTruthy();
});

it('rail displays an actionable error, never a false empty state, even during retry', () => {
  const retry = vi.fn();
  render(<WorkspaceProjectRail projects={[]} userInitial='U' projectsError={new Error('offline')} projectsLoading onRetryProjects={retry} />);
  expect(screen.getByRole('alert')).toBeTruthy();
  expect(screen.queryByText('No projects yet')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Retry loading projects' }));
  expect(retry).toHaveBeenCalledTimes(1);
});

it('rail keeps the last same-identity list visible when a background refresh fails', () => {
  render(<WorkspaceProjectRail projects={[{ id: 'p', name: 'Current project' }]} userInitial='U' projectsError={new Error('offline')} />);
  expect(screen.getByText('Current project')).toBeTruthy();
  expect(screen.getByRole('alert').textContent).toContain('refresh');
});

it('directory placeholders have accessible loading semantics', () => {
  render(<DirectoryLoadingState />);
  expect(screen.getByRole('status', { name: 'Loading files' }).getAttribute('aria-busy')).toBe('true');
  expect(screen.queryByText('Empty folder')).toBeNull();
});

it('folder expansion is isolated by project even when the paths are identical', () => {
  const hook = renderHook(({ project }) => ({ expanded: useIsExpanded(project, 'docs'), ...useExplorerActions() }), { initialProps: { project: 'one' }, wrapper: ExplorerSessionProvider });
  act(() => hook.result.current.ensureExpandedBatch('one', ['docs']));
  expect(hook.result.current.expanded).toBe(true);
  hook.rerender({ project: 'two' });
  expect(hook.result.current.expanded).toBe(false);
  act(() => hook.result.current.toggleExpanded('two', 'docs'));
  expect(hook.result.current.expanded).toBe(true);
  act(() => hook.result.current.toggleExpanded('two', 'docs'));
  hook.rerender({ project: 'one' });
  expect(hook.result.current.expanded).toBe(true);
});

function render(ui: React.ReactElement) { return renderUI(ui, { wrapper: ({ children }) => <WorkspaceNavigationProvider><ResponsiveWorkspaceProvider>{children}</ResponsiveWorkspaceProvider></WorkspaceNavigationProvider> }); }

it('restores expansion when returning to a project and clears it for another account', () => {
  function Tree({ project }: { project: string }) {
    const expanded = useIsExpanded(project, 'docs');
    const actions = useExplorerActions();
    return <button aria-expanded={expanded} onClick={() => actions.toggleExpanded(project, 'docs')}>Folder</button>;
  }
  function Account({ account, project }: { account: string; project: string }) {
    return <ExplorerSessionsProvider key={account}><ExplorerSessionProvider key={project} projectId={project}><Tree project={project} /></ExplorerSessionProvider></ExplorerSessionsProvider>;
  }
  const view = renderUI(<Account account='one' project='a' />);
  fireEvent.click(screen.getByRole('button'));
  view.rerender(<Account account='one' project='b' />);
  expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('false');
  view.rerender(<Account account='one' project='a' />);
  expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('true');
  view.rerender(<Account account='two' project='a' />);
  expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('false');
});
