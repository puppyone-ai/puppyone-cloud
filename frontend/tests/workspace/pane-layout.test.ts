import { describe, expect, it } from 'vitest';
import { DEFAULT_WORKSPACE_INPUT, resolveWorkspaceLayout, type WorkspaceLayoutInput } from '@/features/workspace/paneLayout';
import { createWorkspaceLayoutStore } from '@/features/workspace/layoutStore';
import { getWorkspaceSegments } from '@/features/workspace/WorkspaceLayoutFrame';

function input(width: number, patch: Partial<WorkspaceLayoutInput> = {}): WorkspaceLayoutInput {
  return { ...DEFAULT_WORKSPACE_INPUT, availableWidth: width,
    files: { ...DEFAULT_WORKSPACE_INPUT.files, present: true },
    auxiliary: { ...DEFAULT_WORKSPACE_INPUT.auxiliary, present: true, open: true }, ...patch };
}
describe('workspace space allocation', () => {
  it('keeps the existing desktop preferences while only the document uses spare space', () => {
    for (const width of [1600, 1440, 1280, 1210]) {
      const layout = resolveWorkspaceLayout(input(width));
      expect([layout.projects.width, layout.files.width, layout.auxiliary.width]).toEqual([220, 220, 450]);
      expect(layout.mainWidth).toBe(width - 890);
      expect(layout.sharedHeader).toBe(false);
    }
  });
  it('reclaims only the deficit from Agent, then Files, preserving the document floor', () => {
    const patch = { files: { ...DEFAULT_WORKSPACE_INPUT.files, present: true, preferredWidth: 320 } };
    expect(resolveWorkspaceLayout(input(1250, patch))).toMatchObject({ projects: { width: 220 }, files: { width: 320 }, auxiliary: { width: 390 }, mainWidth: 320 });
    expect(resolveWorkspaceLayout(input(1120, patch))).toMatchObject({ files: { width: 280 }, auxiliary: { width: 300 }, mainWidth: 320 });
  });
  it('changes presentation only when the open panes cannot fit their minima', () => {
    expect(resolveWorkspaceLayout(input(1060))).toMatchObject({ projects: { presentation: 'docked' }, auxiliary: { width: 300 }, mainWidth: 320 });
    expect(resolveWorkspaceLayout(input(1059))).toMatchObject({ projects: { presentation: 'overlay' }, files: { presentation: 'docked' } });
    expect(resolveWorkspaceLayout(input(840))).toMatchObject({ files: { presentation: 'docked' }, auxiliary: { width: 300 }, mainWidth: 320 });
    expect(resolveWorkspaceLayout(input(839))).toMatchObject({ files: { presentation: 'overlay' }, auxiliary: { width: 450 } });
    expect(resolveWorkspaceLayout(input(620))).toMatchObject({ auxiliary: { presentation: 'docked', width: 300 }, mainWidth: 320 });
    expect(resolveWorkspaceLayout(input(619))).toMatchObject({ auxiliary: { presentation: 'overlay', width: 420 }, mainWidth: 619 });
    expect(resolveWorkspaceLayout(input(390))).toMatchObject({ auxiliary: { width: 358 }, files: { width: 350 }, mainWidth: 390 });
  });
  it('releases absent/closed pane budgets and respects a user-collapsed rail', () => {
    expect(resolveWorkspaceLayout(input(900, { auxiliary: { ...DEFAULT_WORKSPACE_INPUT.auxiliary, open: false } })))
      .toMatchObject({ projects: { presentation: 'docked' }, files: { presentation: 'docked' }, mainWidth: 460 });
    expect(resolveWorkspaceLayout(input(850, { files: { ...DEFAULT_WORKSPACE_INPUT.files, present: false } })))
      .toMatchObject({ projects: { presentation: 'docked' }, auxiliary: { width: 310 }, mainWidth: 320 });
    expect(resolveWorkspaceLayout(input(900, { projects: { ...DEFAULT_WORKSPACE_INPUT.projects, collapsed: true } })))
      .toMatchObject({ projects: { presentation: 'docked', width: 56 }, auxiliary: { width: 304 }, mainWidth: 320 });
  });
  it('preserves preferred widths and open intent through a full shrink/restore cycle', () => {
    const store = createWorkspaceLayoutStore(input(1600));
    store.getState().setPane('auxiliary', { preferredWidth: 600 });
    store.getState().setPane('files', { preferredWidth: 300 });
    for (const availableWidth of [1200, 1060, 840, 620, 390, 320, 820, 1600]) {
      store.getState().setEnvironment({ availableWidth, segments: null });
      expect(store.getState().input.auxiliary).toMatchObject({ open: true, preferredWidth: 600 });
      expect(store.getState().input.files.preferredWidth).toBe(300);
    }
    expect(store.getState().layout).toMatchObject({ auxiliary: { width: 600 }, files: { width: 300 } });
  });
  it('enforces bounds and complete accounting through continuous resizing and preference combinations', () => {
    for (const fileWidth of [220, 350, 480]) for (const agentWidth of [300, 450, 800]) {
      for (let width = 280; width <= 1800; width += 7) {
        const layout = resolveWorkspaceLayout(input(width, {
          files: { ...DEFAULT_WORKSPACE_INPUT.files, present: true, preferredWidth: fileWidth },
          auxiliary: { ...DEFAULT_WORKSPACE_INPUT.auxiliary, present: true, open: true, preferredWidth: agentWidth },
        }));
        const docked = [layout.projects, layout.files, layout.auxiliary].filter(p => p.presentation === 'docked');
        expect(docked.reduce((sum, p) => sum + p.width, layout.mainWidth)).toBe(width);
        expect(layout.mainWidth).toBeGreaterThanOrEqual(Math.min(width, 320));
        if (layout.files.presentation === 'docked') expect(layout.files.width).toBeGreaterThanOrEqual(220);
        if (layout.auxiliary.presentation === 'docked') {
          expect(layout.auxiliary.width).toBeGreaterThanOrEqual(300);
          expect(layout.auxiliary.resizeMax).toBeGreaterThanOrEqual(layout.auxiliary.width);
        }
      }
    }
  });
  it('derives drag maxima from the same space budget without hiding a requested size', () => {
    const initial = input(1280);
    const layout = resolveWorkspaceLayout(initial);
    const resized = resolveWorkspaceLayout({ ...initial, auxiliary: { ...initial.auxiliary, preferredWidth: layout.auxiliary.resizeMax } });
    expect(resized.auxiliary.width).toBe(layout.auxiliary.resizeMax);
    expect(resized.mainWidth).toBe(320);
  });
  it('allocates actual native segments and hinge without a second CSS geometry policy', () => {
    expect(resolveWorkspaceLayout(input(840, { segments: { orientation: 'horizontal', first: 400, second: 420, gap: 20 } })))
      .toMatchObject({ projects: { presentation: 'overlay' }, files: { presentation: 'overlay', width: 360 }, auxiliary: { presentation: 'docked', width: 420 }, mainWidth: 400 });
    expect(resolveWorkspaceLayout(input(390, { segments: { orientation: 'vertical', first: 400, second: 400, gap: 20 } })))
      .toMatchObject({ auxiliary: { presentation: 'docked', width: 390 }, mainWidth: 390 });
  });
  it('clips native segment geometry to the workspace content box', () => {
    const segments = [new DOMRect(0, 0, 400, 800), new DOMRect(420, 0, 400, 800)];
    expect(getWorkspaceSegments({ left: 12, top: 0, right: 808, bottom: 800 }, segments))
      .toEqual({ orientation: 'horizontal', first: 388, second: 388, gap: 20 });
    expect(getWorkspaceSegments({ left: 0, top: 0, right: 300, bottom: 800 }, segments)).toBeNull();
  });
  it('budgets file inspectors through the same constraints as Agent', () => {
    const patch = { auxiliary: { ...DEFAULT_WORKSPACE_INPUT.auxiliary }, inspector: { ...DEFAULT_WORKSPACE_INPUT.inspector, present: true, open: true } };
    expect(resolveWorkspaceLayout(input(1280, patch))).toMatchObject({ mainWidth: 390, inspector: { width: 450, presentation: 'docked' } });
    expect(resolveWorkspaceLayout(input(1100, patch))).toMatchObject({ mainWidth: 320, inspector: { width: 340, presentation: 'docked' } });
    expect(resolveWorkspaceLayout(input(390, patch))).toMatchObject({ mainWidth: 390, inspector: { width: 390, presentation: 'overlay' } });
  });
  it('accounts for a local editor and Agent even when both are open', () => {
    for (let width = 320; width <= 1800; width += 11) {
      const layout = resolveWorkspaceLayout(input(width, { inspector: { ...DEFAULT_WORKSPACE_INPUT.inspector, present: true, open: true } }));
      const docked = [layout.projects, layout.files, layout.auxiliary, layout.inspector].filter(p => p.presentation === 'docked');
      expect(docked.reduce((sum, p) => sum + p.width, layout.mainWidth)).toBe(width);
      expect(layout.mainWidth).toBeGreaterThanOrEqual(320);
      if (layout.auxiliary.presentation === 'overlay') expect(layout.overlayOwner).toBe('auxiliary');
      else if (layout.inspector.presentation === 'overlay') expect(layout.overlayOwner).toBe('inspector');
    }
  });
});
