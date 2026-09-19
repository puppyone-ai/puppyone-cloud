import { WorkspaceNavigationProvider } from '@/features/workspace/navigation';
import { ResponsiveWorkspaceProvider } from '@/features/workspace/responsive';
import { useState } from 'react';
import { fireEvent, render as renderUI, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ProjectHeaderContribution, ProjectWorkspaceShell } from '@/components/project/ProjectWorkspaceShell';
import { ProjectSessionProvider, useWorkspaceChat } from '@/features/workspace/session';
import ProjectSettingsDialog from '@/components/project/ProjectSettingsDialog';
import { updateProject } from '@/lib/projectsApi';

const fixture = vi.hoisted(() => ({
  project: { id: 'p', name: '合同合集', org_id: 'org', bound_git_branch: 'main', visibility: 'org', capabilities: ['project.settings.manage', 'project.member.manage'] },
  router: { push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() },
  segment: 'data',
  loading: false,
}));
vi.mock('next/navigation', () => ({ useRouter: () => fixture.router, useSelectedLayoutSegment: () => fixture.segment, usePathname: () => `/projects/p/${fixture.segment}`, useSearchParams: () => new URLSearchParams() }));
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }));
vi.mock('@/contexts/SupabaseAuthProvider', () => ({ useAuth: () => ({ session: { user: { id: 'u' } }, isAuthReady: true }) }));
vi.mock('@/contexts/OrganizationContext', () => ({ useOrganization: () => ({ currentOrg: { id: 'org', name: 'Team' }, members: [{ user_id: 'teammate', display_name: 'Teammate', role: 'member' }], isMembersLoading: false }) }));
vi.mock('@/lib/hooks/useData', () => ({
  useProject: () => ({ project: fixture.project, isLoading: fixture.loading }),
  useProjects: () => ({ projects: [fixture.project], isLoading: fixture.loading }),
  refreshProjects: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('swr', () => ({ default: () => ({ data: [], isLoading: false, mutate: vi.fn().mockResolvedValue(undefined) }) }));
vi.mock('@/lib/projectsApi', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/projectsApi')>(),
  getProjectShareInfo: vi.fn().mockResolvedValue({ share_token: null }),
  updateProject: vi.fn().mockResolvedValue(undefined),
}));

beforeEach(() => {
  fixture.project.capabilities = ['project.settings.manage', 'project.member.manage'];
  fixture.segment = 'data';
  fixture.loading = false;
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.spyOn(HTMLElement.prototype, 'getClientRects').mockReturnValue([{ width: 1, height: 1 }] as unknown as DOMRectList);
});
afterEach(() => vi.unstubAllGlobals());

function WorkInProgress() {
  const [draft, setDraft] = useState('Unsaved file edits');
  const [chatOpen] = useWorkspaceChat();
  return <>
    <ProjectHeaderContribution canManageSettings pathSegments={[{ label: '合同合集' }, { label: 'notes.md' }]} />
    <textarea aria-label='File draft' value={draft} onChange={event => setDraft(event.target.value)} />
    {chatOpen && <textarea aria-label='Chat draft' defaultValue='Unsent message' />}
  </>;
}

it.each(['data', 'history'])('opens settings over %s without navigation or resetting file/chat state', async segment => {
  fixture.segment = segment;
  render(<ProjectSessionProvider projectId='p'><ProjectWorkspaceShell projectId='p'><WorkInProgress /></ProjectWorkspaceShell></ProjectSessionProvider>);
  const file = screen.getByRole('textbox', { name: 'File draft' }) as HTMLTextAreaElement;
  fireEvent.change(file, { target: { value: 'Keep this unsaved edit' } });
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }));
  const chat = screen.getByRole('textbox', { name: 'Chat draft' }) as HTMLTextAreaElement;
  fireEvent.change(chat, { target: { value: 'Keep this unsent message' } });
  const trigger = screen.getByRole('button', { name: 'Project settings' });
  trigger.focus();
  fireEvent.click(trigger);
  await screen.findByText('Project Name');
  const dialog = screen.getByRole('dialog', { name: 'Project settings' });
  expect(document.body.contains(dialog)).toBe(true);
  expect(fixture.router.push).not.toHaveBeenCalled();
  expect(fixture.router.replace).not.toHaveBeenCalled();
  expect(screen.getByRole('link', { name: segment === 'history' ? 'Git' : 'Files' }).getAttribute('aria-current')).toBe('page');

  const close = within(dialog).getByRole('button', { name: 'Close project settings' });
  // Lazy settings content can commit before its focus effect under load.
  await waitFor(() => expect(document.activeElement).toBe(close));
  const last = within(dialog).getByRole('button', { name: 'Delete Project' });
  last.focus();
  fireEvent.keyDown(last, { key: 'Tab' });
  expect(document.activeElement).toBe(close);
  fireEvent.keyDown(close, { key: 'Tab', shiftKey: true });
  expect(document.activeElement).toBe(last);
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(trigger);
  expect(screen.getByRole('textbox', { name: 'File draft' })).toBe(file);
  expect(file.value).toBe('Keep this unsaved edit');
  expect(screen.getByRole('textbox', { name: 'Chat draft' })).toBe(chat);
  expect(chat.value).toBe('Keep this unsent message');
});

it('keeps nested edit/member dialogs above settings and Escape closes only the active layer', async () => {
  const close = vi.fn();
  render(<ProjectSettingsDialog projectId='p' onClose={close} />);
  await screen.findByText('Project Name');
  fireEvent.click(screen.getAllByRole('button', { name: 'Edit' })[0]);
  expect(screen.getAllByRole('dialog')).toHaveLength(2);
  const input = screen.getByDisplayValue('合同合集');
  fireEvent.keyDown(input, { key: 'Escape' });
  expect(screen.getAllByRole('dialog')).toHaveLength(1);
  expect(close).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Add Member' }));
  const picker = screen.getByRole('dialog', { name: 'Add project members' });
  expect(picker.parentElement?.style.zIndex).toBe('11001');
  fireEvent.keyDown(picker, { key: 'Escape' });
  expect(screen.queryByRole('dialog', { name: 'Add project members' })).toBeNull();
  expect(close).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Close project settings' }));
  expect(close).toHaveBeenCalledOnce();
});

it('retains branch save behavior and cancels inline editing before closing settings', async () => {
  const close = vi.fn();
  render(<ProjectSettingsDialog projectId='p' onClose={close} />);
  await screen.findByText('Project Name');
  fireEvent.click(screen.getAllByRole('button', { name: 'Edit' })[1]);
  const branch = screen.getByRole('textbox', { name: 'boundGitBranch' });
  fireEvent.change(branch, { target: { value: 'release' } });
  fireEvent.keyDown(branch, { key: 'Escape' });
  expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  expect(close).not.toHaveBeenCalled();
  expect(updateProject).not.toHaveBeenCalled();

  fireEvent.click(screen.getAllByRole('button', { name: 'Edit' })[1]);
  fireEvent.change(screen.getByRole('textbox', { name: 'boundGitBranch' }), { target: { value: 'release' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(updateProject).toHaveBeenCalledWith('p', { bound_git_branch: 'release' }));
  await screen.findByText('Default branch updated.');
  expect(close).not.toHaveBeenCalled();
});

it('can dismiss while loading and preserves the project permission check', async () => {
  fixture.loading = true;
  const close = vi.fn();
  const view = render(<ProjectSettingsDialog projectId='p' onClose={close} />);
  fireEvent.click(screen.getByRole('button', { name: 'Close project settings' }));
  expect(close).toHaveBeenCalledOnce();
  fixture.loading = false;
  fixture.project.capabilities = [];
  view.rerender(<ProjectSettingsDialog projectId='p' onClose={close} />);
  await screen.findByText('Project settings are restricted');
  expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Delete Project' })).toBeNull();
});

function render(ui: React.ReactElement) { return renderUI(ui, { wrapper: ({ children }) => <WorkspaceNavigationProvider><ResponsiveWorkspaceProvider>{children}</ResponsiveWorkspaceProvider></WorkspaceNavigationProvider> }); }
