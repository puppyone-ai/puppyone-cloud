import { act, fireEvent, render, screen } from '@testing-library/react';
import { memo, useEffect, useState, type ComponentProps } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { ResponsiveWorkspaceProvider, useWorkspaceActions } from '@/features/workspace/responsive';
import { createWorkspaceLayoutStore } from '@/features/workspace/layoutStore';
import { DEFAULT_WORKSPACE_INPUT } from '@/features/workspace/paneLayout';
import { DataWorkspaceSurface } from '@/features/files/components/DataWorkspaceSurface';

const measurements = vi.hoisted(() => ({ editorRenders: 0, editorMounts: 0, editorUnmounts: 0, actionConsumerRenders: 0 }));
// Exercise the real responsive provider, data surface and drawer wrappers.
// Replace I/O-bearing leaves with a stateful editor probe; this measures the
// layout boundary, not CodeMirror/Monaco internals or browser paint duration.
vi.mock('@/features/files/components/EditorArea', () => ({
  EditorArea: function EditorProbe() {
    measurements.editorRenders++;
    const [draft, setDraft] = useState('draft');
    useEffect(() => { measurements.editorMounts++; return () => { measurements.editorUnmounts++; }; }, []);
    return <textarea aria-label='Editor probe' value={draft} onChange={event => setDraft(event.target.value)} />;
  },
}));
vi.mock('@/features/files/components/DataPageDialogs', () => ({ DataPageDialogs: () => null }));
vi.mock('@/features/files/components/DataPageOverlays', () => ({ DataPageOverlays: () => null }));
vi.mock('@/features/files/components/BulkDeleteDialog', () => ({ BulkDeleteDialog: () => null }));
vi.mock('@/features/files/components/SelectionActionBar', () => ({ SelectionActionBar: () => null }));
vi.mock('@/features/files/components/explorer', () => ({ DataExplorerPane: () => <nav>Directory probe</nav> }));
vi.mock('@/features/files/components/right-panel', () => ({ DataPageRightPanel: () => null }));
vi.mock('@/features/projects/components/EmptyWorkspaceState', () => ({ EmptyWorkspaceState: () => null }));

const surfaceProps = {
  dialogsProps: {}, overlaysProps: {}, bulkDeleteProps: {}, selectionProps: {},
  explorer: { hidden: false, props: { onNavigate() {} } },
  content: { isEditorView: true, editorAreaProps: {}, isFolderView: false },
  rightPanelProps: {},
// Unused leaf props are intentionally empty because those leaves are mocked.
} as unknown as ComponentProps<typeof DataWorkspaceSurface>;

const CommandsOnly = memo(function CommandsOnly() {
  const { setProjectsOpen, setFilesOpen } = useWorkspaceActions();
  measurements.actionConsumerRenders++;
  return <><button onClick={() => setProjectsOpen(true)}>Projects</button><button onClick={() => setProjectsOpen(false)}>Close projects</button><button onClick={() => setFilesOpen(true)}>Files</button></>;
});

afterEach(() => vi.unstubAllGlobals());

it('isolates navigation and breakpoint renders from the editor and action-only consumers', () => {
  Object.assign(measurements, { editorRenders: 0, editorMounts: 0, editorUnmounts: 0, actionConsumerRenders: 0 });
  const layoutStore = createWorkspaceLayoutStore({ files: { ...DEFAULT_WORKSPACE_INPUT.files, present: true }, auxiliary: { ...DEFAULT_WORKSPACE_INPUT.auxiliary, present: true, open: true } });
  render(<ResponsiveWorkspaceProvider layoutStore={layoutStore}><CommandsOnly /><DataWorkspaceSurface {...surfaceProps} /></ResponsiveWorkspaceProvider>);
  const editor = screen.getByLabelText('Editor probe');
  fireEvent.change(editor, { target: { value: 'unsaved audit draft' } });
  const rows: Record<string, string | number>[] = [];
  function sample(action: string, run: () => void) {
    const before = { ...measurements };
    act(run);
    expect(measurements.editorRenders - before.editorRenders, action).toBe(0);
    expect(measurements.actionConsumerRenders - before.actionConsumerRenders, action).toBe(0);
    rows.push({ action, editorRenders: measurements.editorRenders - before.editorRenders, commandRenders: measurements.actionConsumerRenders - before.actionConsumerRenders, editorRemounts: measurements.editorMounts - before.editorMounts });
    expect(screen.getByLabelText('Editor probe')).toBe(editor);
    expect((editor as HTMLTextAreaElement).value).toBe('unsaved audit draft');
    expect(measurements.editorUnmounts).toBe(0);
  }
  const resize = (availableWidth: number) => layoutStore.getState().setEnvironment({ availableWidth, segments: null });
  sample('1280 -> 1200, same mode', () => resize(1200));
  sample('open unrelated project navigation', () => fireEvent.click(screen.getByText('Projects')));
  sample('close unrelated project navigation', () => fireEvent.click(screen.getByText('Close projects')));
  sample('1200 -> 1023, cross breakpoint', () => resize(1023));
  sample('1023 -> 800, same mode', () => resize(800));
  sample('open file sidebar', () => fireEvent.click(screen.getByRole('button', { name: /^Files$/ })));
  sample('800 -> 639, cross with sidebar open', () => resize(639));
  sample('639 -> 1280', () => resize(1280));
  // Both lifecycle and subscription isolation are regression contracts.
  expect(measurements.editorMounts).toBe(1);
  console.info('Responsive layout audit:', JSON.stringify(rows));
});
