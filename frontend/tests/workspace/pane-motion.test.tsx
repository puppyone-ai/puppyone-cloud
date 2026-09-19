import { act, fireEvent, render, screen } from '@testing-library/react';
import { StrictMode, useState } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ResponsiveWorkspaceProvider } from '@/features/workspace/responsive';
import { createWorkspaceLayoutStore } from '@/features/workspace/layoutStore';
import { WorkspaceAuxiliaryRegion } from '@/components/sidebar/WorkspaceAuxiliaryRegion';
import { WorkspaceInspectorRegion } from '@/components/sidebar/WorkspaceInspectorRegion';
import { PANE_MOTION_MS } from '@/features/workspace/usePaneMotion';

beforeEach(() => vi.useFakeTimers());
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function setup(width = 820, inspector = false) {
  const store = createWorkspaceLayoutStore({ availableWidth: width });
  function Harness() {
    const [visible, setVisible] = useState(true);
    return <><button onClick={() => setVisible(value => !value)}>Toggle</button>
      {inspector
        ? <WorkspaceInspectorRegion visible={visible} onClose={() => setVisible(false)} background='var(--po-panel)'>{visible && <input aria-label='Draft' defaultValue='Unsaved' />}</WorkspaceInspectorRegion>
        : <WorkspaceAuxiliaryRegion visible={visible} onClose={() => setVisible(false)} label='Agent' trigger='chat'><input aria-label='Draft' defaultValue='Unsaved' /></WorkspaceAuxiliaryRegion>}
    </>;
  }
  render(<StrictMode><ResponsiveWorkspaceProvider layoutStore={store}><Harness /></ResponsiveWorkspaceProvider></StrictMode>);
  const panel = document.querySelector<HTMLElement>('[data-resizable-panel]')!;
  const toggle = () => fireEvent.click(screen.getByText('Toggle'));
  const settle = () => act(() => vi.advanceTimersByTime(PANE_MOTION_MS + 40));
  return { store, panel, toggle, settle };
}

it('retains exit geometry while immediately removing closed content from interaction', () => {
  const { store, panel, toggle, settle } = setup();
  const before = store.getState().layout;
  const draft = screen.getByLabelText('Draft');
  toggle();
  expect(panel.dataset.motion).toBe('closing');
  expect(panel.hasAttribute('inert')).toBe(true);
  expect(panel.getAttribute('aria-hidden')).toBe('true');
  expect(store.getState().layout).toEqual(before);
  settle();
  expect(panel.dataset.motion).toBe('idle');
  expect(store.getState().input.auxiliary.open).toBe(false);
  expect(store.getState().layout.mainWidth).toBeGreaterThan(before.mainWidth);
  toggle();
  expect(panel.dataset.motion).toBe('opening');
  expect(panel.hasAttribute('inert')).toBe(false);
  expect(screen.getByLabelText('Draft')).toBe(draft);
  settle();
  expect(panel.dataset.motion).toBe('idle');
});

it('reverses a rapid close/open without a stale timer closing the reopened region', () => {
  const { store, panel, toggle, settle } = setup(390);
  toggle();
  act(() => vi.advanceTimersByTime(80));
  toggle();
  act(() => vi.advanceTimersByTime(160));
  expect(panel.dataset.motion).toBe('opening');
  expect(store.getState().input.auxiliary.open).toBe(true);
  settle();
  expect(panel.dataset.visible).toBe('true');
  expect(store.getState().input.auxiliary.open).toBe(true);
});

it('settles an interrupted exit immediately when the workspace resizes', () => {
  const { store, panel, toggle } = setup();
  toggle();
  act(() => store.getState().setEnvironment({ availableWidth: 390, segments: null }));
  expect(panel.dataset.motion).toBe('idle');
  expect(store.getState().input.auxiliary.open).toBe(false);
  toggle();
  act(() => store.getState().setEnvironment({ availableWidth: 1280, segments: null }));
  expect(panel.dataset.motion).toBe('idle');
  expect(store.getState().input.auxiliary.open).toBe(true);
  expect(store.getState().layout.auxiliary.width).toBe(450);
});

it('finishes on the panel transition, ignoring descendant opacity transitions', () => {
  const { store, panel, toggle } = setup();
  toggle();
  const end = (target: Element, propertyName: string) => {
    const event = new Event('transitionend', { bubbles: true });
    Object.defineProperty(event, 'propertyName', { value: propertyName });
    fireEvent(target, event);
  };
  end(panel.querySelector('[data-panel-content]')!, 'opacity');
  expect(panel.dataset.motion).toBe('closing');
  end(panel, 'width');
  expect(panel.dataset.motion).toBe('idle');
  expect(store.getState().input.auxiliary.open).toBe(false);
});

it('keeps outgoing inspector content until its exit, then unmounts it', () => {
  const { panel, toggle, settle } = setup(390, true);
  const draft = screen.getByLabelText('Draft');
  toggle();
  expect(panel.dataset.motion).toBe('closing');
  expect(screen.getByLabelText('Draft')).toBe(draft);
  settle();
  expect(screen.queryByLabelText('Draft')).toBeNull();
});

it('honors reduced motion without delaying space release', () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const { store, panel, toggle } = setup();
  toggle();
  expect(panel.dataset.motion).toBe('idle');
  expect(store.getState().input.auxiliary.open).toBe(false);
  toggle();
  expect(panel.dataset.motion).toBe('idle');
});
