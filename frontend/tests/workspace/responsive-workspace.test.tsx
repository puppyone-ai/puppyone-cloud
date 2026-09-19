import { act, fireEvent, render, screen } from '@testing-library/react';
import { useEffect, useState, type ReactNode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ResponsiveWorkspaceProvider as Provider, useWorkspaceActions, useWorkspaceNavigation } from '@/features/workspace/responsive';
import { useWorkspaceRegions } from '@/features/workspace/regions';
import { ResponsiveDrawer } from '@/components/sidebar/ResponsiveDrawer';
import { ItemContextMenu } from '@/features/files/components/explorer/ExplorerRowMenus';

import { createWorkspaceLayoutStore, useWorkspaceLayout, type WorkspaceLayoutStore } from '@/features/workspace/layoutStore';
import { DEFAULT_WORKSPACE_INPUT } from '@/features/workspace/paneLayout';

let layoutStore: WorkspaceLayoutStore;
let width = 1280;
let segmented = false;
let viewport: EventTarget & { height: number; offsetTop: number; scale: number };
beforeEach(() => {
  width = 1280;
  segmented = false;
  viewport = Object.assign(new EventTarget(), { height: 844, offsetTop: 0, scale: 1 });
  vi.stubGlobal('visualViewport', viewport);
  vi.spyOn(HTMLElement.prototype, 'getClientRects').mockReturnValue([{ width: 20, height: 20 }] as unknown as DOMRectList);
});
afterEach(() => vi.unstubAllGlobals());

function environment() { return { availableWidth: width, segments: segmented ? { orientation: 'vertical' as const, first: 400, second: 400, gap: 20 } : null }; }
function ResponsiveWorkspaceProvider({ children }: { children: ReactNode }) {
  const [store] = useState(() => createWorkspaceLayoutStore({ ...environment(), files: { ...DEFAULT_WORKSPACE_INPUT.files, present: true }, auxiliary: { ...DEFAULT_WORKSPACE_INPUT.auxiliary, present: true, open: true } }));
  layoutStore = store;
  return <Provider layoutStore={store}>{children}</Provider>;
}
function resize(next: number) { act(() => { width = next; layoutStore.getState().setEnvironment(environment()); }); }

function Harness({ mounts }: { mounts: () => void }) {
  const presentation = useWorkspaceLayout(layout => [layout.projects, layout.files, layout.auxiliary].map(pane => pane.presentation).join('/'));
  const filesOpen = useWorkspaceNavigation(state => state.filesOpen);
  const { setFilesOpen } = useWorkspaceActions();
  const regions = useWorkspaceRegions();
  return <>
    <main ref={regions.appContent} data-testid='background'><span data-testid='presentation'>{presentation}</span>
      <button onClick={() => setFilesOpen(true)}>Browse files</button>
    </main>
    <ResponsiveDrawer id='files' label='Files' open={filesOpen} onClose={() => setFilesOpen(false)}>
      <StatefulExplorer mounts={mounts} />
    </ResponsiveDrawer>
  </>;
}
function StatefulExplorer({ mounts }: { mounts: () => void }) {
  const [draft, setDraft] = useState('');
  useEffect(mounts, [mounts]);
  return <input aria-label='Preserved state' value={draft} onChange={event => setDraft(event.target.value)} />;
}

it('keeps the same mounted state through phone, unfolded, and desktop transitions', () => {
  const mounts = vi.fn();
  render(<ResponsiveWorkspaceProvider><Harness mounts={mounts} /></ResponsiveWorkspaceProvider>);
  fireEvent.change(screen.getByLabelText('Preserved state'), { target: { value: 'unsaved selection' } });
  for (const [size, presentation] of [[390, 'overlay/overlay/overlay'], [820, 'overlay/overlay/docked'], [619, 'overlay/overlay/overlay'], [620, 'overlay/overlay/docked'], [1280, 'docked/docked/docked']] as const) {
    resize(size);
    expect(screen.getByTestId('presentation').textContent).toBe(presentation);
    expect((screen.getByLabelText('Preserved state') as HTMLInputElement).value).toBe('unsaved selection');
  }
  expect(mounts).toHaveBeenCalledOnce();
});

it('contains keyboard focus, closes on Escape, and restores the invoking control', () => {
  width = 390;
  render(<ResponsiveWorkspaceProvider><Harness mounts={vi.fn()} /></ResponsiveWorkspaceProvider>);
  const trigger = screen.getByText('Browse files');
  trigger.focus();
  fireEvent.click(trigger);
  expect(screen.getByRole('dialog', { name: 'Files' })).toBeTruthy();
  expect(screen.getByTestId('background').hasAttribute('inert')).toBe(true);
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close files navigation' }));
  const input = screen.getByLabelText('Preserved state');
  input.focus();
  fireEvent.keyDown(input, { key: 'Tab' });
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close files navigation' }));
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.getByTestId('background').hasAttribute('inert')).toBe(false);
  expect(document.activeElement).toBe(trigger);
});

it('keeps the backdrop actionable and preserves open intent across an unfold', () => {
  width = 390;
  render(<ResponsiveWorkspaceProvider><Harness mounts={vi.fn()} /></ResponsiveWorkspaceProvider>);
  fireEvent.click(screen.getByText('Browse files'));
  const backdrop = screen.getByRole('button', { name: 'Close files' });
  expect(backdrop.closest('[inert]')).toBeNull();
  fireEvent.click(backdrop);
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(screen.getByText('Browse files'));
  resize(820);
  expect(screen.queryByRole('dialog')).not.toBeNull();
  resize(1280);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.getByTestId('background').hasAttribute('inert')).toBe(false);
  resize(390);
  expect(screen.queryByRole('dialog')).not.toBeNull();
});

it('follows the visual viewport without breakpoint effects, ignores pinch zoom, and cleans up on unmount', () => {
  vi.useFakeTimers();
  width = 390;
  const view = render(<ResponsiveWorkspaceProvider><Harness mounts={vi.fn()} /></ResponsiveWorkspaceProvider>);
  expect(document.documentElement.style.getPropertyValue('--workspace-viewport-height')).toBe('844px');
  const writes = vi.spyOn(document.documentElement.style, 'setProperty');
  act(() => { viewport.dispatchEvent(new Event('resize')); viewport.dispatchEvent(new Event('scroll')); vi.runAllTimers(); });
  expect(writes).not.toHaveBeenCalled();
  act(() => { viewport.height = 410; viewport.offsetTop = 25; viewport.dispatchEvent(new Event('resize')); vi.runAllTimers(); });
  expect(document.documentElement.style.getPropertyValue('--workspace-viewport-height')).toBe('410px');
  expect(document.documentElement.style.getPropertyValue('--workspace-viewport-top')).toBe('25px');
  act(() => { viewport.scale = 2; viewport.height = 200; viewport.dispatchEvent(new Event('resize')); vi.runAllTimers(); });
  expect(document.documentElement.style.getPropertyValue('--workspace-viewport-height')).toBe('410px');
  resize(1280);
  expect(document.documentElement.style.getPropertyValue('--workspace-viewport-height')).toBe('410px');
  view.unmount();
  expect(document.documentElement.style.getPropertyValue('--workspace-viewport-height')).toBe('');
  vi.useRealTimers();
});

it('keeps a file menu inside the phone viewport and dismisses it before its drawer', () => {
  width = 320;
  vi.stubGlobal('innerWidth', 320);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const menu = this.getAttribute('role') === 'menu';
    return { x: 280, y: 766, left: 280, top: 766, width: menu ? 160 : 44, height: menu ? 140 : 44, bottom: 810, right: 324, toJSON() {} };
  });
  const closeDrawer = vi.fn();
  const { container } = render(<ResponsiveWorkspaceProvider>
    <ResponsiveDrawer id='files' label='Files' open onClose={closeDrawer}>
      <ItemContextMenu itemId='notes.md' itemName='notes.md' onRename={() => {}} />
    </ResponsiveDrawer>
  </ResponsiveWorkspaceProvider>);
  const trigger = container.querySelector<HTMLButtonElement>('[aria-haspopup="menu"]')!;
  fireEvent.click(trigger);
  const menu = screen.getByRole('menu');
  expect(Number.parseFloat(menu.style.left) + 160).toBeLessThanOrEqual(312);
  expect(Number.parseFloat(menu.style.top) + 140).toBeLessThanOrEqual(836);
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('menu')).toBeNull();
  expect(closeDrawer).not.toHaveBeenCalled();
  expect(document.activeElement).toBe(trigger);
});

it('keeps the shared Header interactive while the left file sidebar covers the document', () => {
  width = 390;
  function Project() {
    const filesOpen = useWorkspaceNavigation(state => state.filesOpen);
    const { setFilesOpen } = useWorkspaceActions();
    const regions = useWorkspaceRegions();
    return <div><header data-workspace-header><button onClick={() => setFilesOpen(!filesOpen)}>Toggle files</button></header>
      <main><ResponsiveDrawer id='files' label='Files' belowHeader open={filesOpen} onClose={() => setFilesOpen(false)}><input aria-label='Directory filter' /></ResponsiveDrawer>
        <div ref={regions.editor} data-testid='document'>Current document</div>
      </main>
    </div>;
  }
  render(<ResponsiveWorkspaceProvider><Project /></ResponsiveWorkspaceProvider>);
  const trigger = screen.getByText('Toggle files');
  trigger.focus();
  fireEvent.click(trigger);
  expect(screen.getByRole('complementary', { name: 'Files' })).toBeTruthy();
  expect(trigger.closest('[inert]')).toBeNull();
  expect(screen.getByTestId('document').hasAttribute('inert')).toBe(true);
  fireEvent.click(trigger);
  expect(screen.queryByRole('complementary')).toBeNull();
  expect(screen.getByTestId('document').hasAttribute('inert')).toBe(false);
  expect(document.activeElement).toBe(trigger);
});


it('treats native display segments as separate work areas without changing navigation intent', () => {
  width = 390;
  render(<ResponsiveWorkspaceProvider><Harness mounts={vi.fn()} /></ResponsiveWorkspaceProvider>);
  fireEvent.click(screen.getByText('Browse files'));
  const input = screen.getByLabelText('Preserved state');
  fireEvent.change(input, { target: { value: 'retained while folding' } });
  act(() => { segmented = true; layoutStore.getState().setEnvironment(environment()); });
  expect(screen.getByTestId('presentation').textContent).toBe('overlay/overlay/docked');
  expect(screen.getByRole('dialog', { name: 'Files' })).toBeTruthy();
  expect(screen.getByLabelText('Preserved state')).toBe(input);
  act(() => { segmented = false; layoutStore.getState().setEnvironment(environment()); });
  expect(screen.getByTestId('presentation').textContent).toBe('overlay/overlay/overlay');
  expect((input as HTMLInputElement).value).toBe('retained while folding');
});
