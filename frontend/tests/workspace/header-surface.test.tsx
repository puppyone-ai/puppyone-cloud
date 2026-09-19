import { ResponsiveWorkspaceProvider } from '@/features/workspace/responsive';
import { act, fireEvent, render as renderUI, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ProjectHeaderBreadcrumbs, ProjectsHeader } from '@/components/ProjectsHeader';
import { ProjectSessionProvider } from '@/features/workspace/session';
import { FileViewerHeaderActions } from '@/app/(main)/projects/[projectId]/data/components/FileViewerHeaderActions';

const router = { prefetch: vi.fn() };
vi.mock('next/navigation', () => ({ useRouter: () => router }));

let notifyResize: () => void;
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { notifyResize = callback; }
    observe() {}
    disconnect() {}
  });
});
afterEach(() => vi.unstubAllGlobals());

it.each([
  ['light', 'files'], ['light', 'git'], ['dark', 'files'], ['dark', 'git'],
] as const)('uses themed chrome without changing header geometry in %s / %s', (theme, activeView) => {
  render(
    <div data-theme={theme}>
      <ProjectSessionProvider projectId='p'>
        <ProjectsHeader projectId='p' activeView={activeView} pathSegments={[{ label: 'Project' }]} actionSlot={<button>Chat</button>} />
      </ProjectSessionProvider>
    </div>,
  );
  const header = screen.getByRole('banner');
  expect(header.style.background).toBe('var(--po-header)');
  expect(header.style.height).toBe('var(--project-header-height, 46px)');
  expect(header.style.flexGrow).toBe('0');
  expect(header.style.paddingLeft).toBe('var(--project-header-padding, 16px)');
  expect(screen.getByRole('navigation', { name: 'Project views' })).toBeTruthy();
  expect(screen.getByRole('link', { name: activeView === 'files' ? 'Files' : 'Git' }).getAttribute('aria-current')).toBe('page');
  expect(screen.getByRole('button', { name: 'Chat' })).toBeTruthy();
});

it('collapses parent folders as space shrinks, preserves guarded navigation, and restores the full path', () => {
  let availableWidth = 700;
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const width = this.getAttribute('aria-label') === 'Project path' ? availableWidth : 600;
    return { width, height: 24, top: 0, left: 0, bottom: 24, right: width, x: 0, y: 0, toJSON() {} };
  });
  const navigate = vi.fn();
  const openSettings = vi.fn();
  render(<ProjectHeaderBreadcrumbs onOpenSettings={openSettings} pathSegments={[
    { label: '中文项目名称', href: '/projects/p/data' },
    { label: '合同资料', href: '/projects/p/data/contracts', onClick: navigate },
    { label: '2026', href: '/projects/p/data/contracts/2026' },
    { label: '非常长的当前文件名称.md' },
  ]} />);
  expect(screen.getByRole('link', { name: '合同资料' })).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Show parent folders' })).toBeNull();

  availableWidth = 260;
  act(() => notifyResize());
  expect(screen.queryByRole('link', { name: '合同资料' })).toBeNull();
  expect(screen.getByRole('link', { name: '中文项目名称' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Project settings' }));
  expect(openSettings).toHaveBeenCalledOnce();
  expect(screen.queryByRole('link', { name: 'Project settings' })).toBeNull();
  expect(screen.getByTitle('非常长的当前文件名称.md').getAttribute('aria-current')).toBe('page');

  const trigger = screen.getByRole('button', { name: 'Show parent folders' });
  fireEvent.click(trigger);
  const folder = screen.getByRole('link', { name: '合同资料' });
  expect(document.activeElement).toBe(folder);
  fireEvent.click(folder);
  expect(navigate).toHaveBeenCalledOnce();
  expect(trigger.getAttribute('aria-expanded')).toBe('false');
  fireEvent.click(trigger);
  fireEvent.keyDown(screen.getByRole('link', { name: '合同资料' }), { key: 'Escape' });
  expect(trigger.getAttribute('aria-expanded')).toBe('false');
  expect(document.activeElement).toBe(trigger);

  availableWidth = 700;
  act(() => notifyResize());
  expect(screen.queryByRole('button', { name: 'Show parent folders' })).toBeNull();
  expect(screen.getByRole('link', { name: '合同资料' })).toBeTruthy();
  expect(screen.getByRole('link', { name: '2026' })).toBeTruthy();
});

it('does not render project settings without an authorized action', () => {
  render(<ProjectHeaderBreadcrumbs pathSegments={[{ label: 'Project' }]} />);
  expect(screen.queryByRole('button', { name: 'Project settings' })).toBeNull();
});

it('keeps save and view-mode controls functional in the header', () => {
  const onSave = vi.fn();
  const onModeChange = vi.fn();
  render(<ProjectSessionProvider projectId='p'>
    <ProjectsHeader projectId='p' pathSegments={[{ label: 'Project' }, { label: 'notes.md' }]} actionSlot={
      <FileViewerHeaderActions projectId='p' filePath='notes.md' viewerId='markdown-editor' editable
        markdownViewMode='wysiwyg' onMarkdownViewModeChange={onModeChange} saveStatus='dirty' onSave={onSave}
        editorType='table' onEditorTypeChange={() => {}} htmlMode='preview' onHtmlModeChange={() => {}}
        csvViewMode='edit' onCsvViewModeChange={() => {}} actionsSlot={<button>File actions</button>} />
    } />
  </ProjectSessionProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  expect(onSave).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByTitle('View mode: Live view'));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Source code' }));
  expect(onModeChange).toHaveBeenCalledWith('source');
  expect(screen.getByRole('button', { name: 'File actions' })).toBeTruthy();
  // Desktop keeps the existing geometry even with all editing actions present.
  expect(screen.getByRole('banner').style.height).toBe('var(--project-header-height, 46px)');
});

function render(ui: React.ReactElement) { return renderUI(ui, { wrapper: ResponsiveWorkspaceProvider }); }
