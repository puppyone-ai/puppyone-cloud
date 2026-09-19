// @vitest-environment node
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, it } from 'vitest';

function files(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => entry.isDirectory()
    ? files(resolve(directory, entry.name)) : /\.tsx?$/.test(entry.name) ? [resolve(directory, entry.name)] : []);
}
it('extracted business domains cannot depend on Next route entry modules', () => {
  for (const path of files(resolve('features'))) {
    const source = readFileSync(path, 'utf8');
    expect(source, path).not.toMatch(/(?:from|import\s*\()\s*['"][^'"]*(?:@\/app\/|\/app\/|page\.tsx|layout\.tsx)/);
  }
});
it('the project layout is the single persistent auxiliary-sidebar owner', () => {
  const project = resolve('app/(main)/projects/[projectId]');
  const layout = readFileSync(resolve(project, 'layout.tsx'), 'utf8');
  expect(layout).toContain('<ProjectAuxiliarySidebar');
  expect(layout).toContain('<ProjectWorkspaceShell');
  const auxiliary = readFileSync(resolve(project, '_components/ProjectAuxiliarySidebar.tsx'), 'utf8');
  expect(auxiliary).toContain('<WorkspaceAuxiliaryRegion');
  expect(auxiliary).toContain('<ProjectChatPanel');
  expect(auxiliary).toContain('<ProjectAccessPanel');
  const headerShell = readFileSync(resolve('components/project/ProjectWorkspaceShell.tsx'), 'utf8');
  expect(headerShell).toContain('<ProjectsHeader');
  expect(headerShell).toContain('<ProjectAuxiliaryActions');
  expect(readFileSync(resolve(project, 'history/page.tsx'), 'utf8')).not.toContain('<ProjectsHeader');
  expect(readFileSync(resolve(project, 'data/components/DataWorkspaceSurface.tsx'), 'utf8')).not.toContain('<ProjectsHeader');
  expect(readFileSync(resolve('components/ProjectsHeader.tsx'), 'utf8')).toContain("flex: '0 0 auto'");
  for (const view of ['history', 'access']) expect(readFileSync(resolve(project, view, 'page.tsx'), 'utf8')).not.toContain('<ProjectChatSidebar');
  expect(readFileSync(resolve(project, 'access/page.tsx'), 'utf8')).not.toContain('<ProjectsHeader');
  expect(readFileSync(resolve('components/ProjectsHeader.tsx'), 'utf8')).not.toContain("['access', 'Access'");
  expect(readFileSync(resolve('features/workspace/session.tsx'), 'utf8')).not.toContain('create<ProjectSession>');
});

it('the avatar opens global settings without a duplicate rail settings button', () => {
  const projectRail = readFileSync(resolve('components/sidebar/WorkspaceProjectRail.tsx'), 'utf8');
  expect(projectRail).toContain('onClick={() => setUserMenuOpen(true)}');
  expect(projectRail).toContain("initialTab='account'");
  expect(projectRail).not.toContain("aria-label='Settings'");
  expect(projectRail).not.toContain("onClick={() => router.push('/settings/appearance')}");

  const userMenu = readFileSync(resolve('components/UserMenuPanel.tsx'), 'utf8');
  expect(userMenu).toContain("initialTab = 'account'");
  expect(userMenu).toContain('if (isOpen) setActiveTab(initialTab)');
});
