/** Geometry contract for the Web workbench. No viewport/device breakpoints.
 * Preferences and open intent are inputs; this function never writes them. */
export type PanePresentation = 'docked' | 'overlay' | 'absent';
export type PaneConstraint = { present: boolean; preferredWidth: number; minWidth: number; maxWidth: number };
export type WorkspaceSegments = {
  orientation: 'horizontal' | 'vertical';
  first: number;
  second: number;
  gap: number;
};
export type WorkspaceLayoutInput = {
  availableWidth: number;
  mainMinWidth: number;
  projects: PaneConstraint & { collapsed: boolean; collapsedWidth: number };
  files: PaneConstraint;
  auxiliary: PaneConstraint & { open: boolean };
  inspector: PaneConstraint & { open: boolean };
  segments: WorkspaceSegments | null;
};
export type ResolvedPane = { presentation: PanePresentation; width: number; resizeMax: number };
export type WorkspaceLayout = {
  availableWidth: number;
  mainWidth: number;
  projects: ResolvedPane;
  files: ResolvedPane;
  auxiliary: ResolvedPane;
  inspector: ResolvedPane;
  overlayOwner: 'auxiliary' | 'inspector' | null;
  sharedHeader: boolean;
  segments: WorkspaceSegments | null;
};

export const DEFAULT_WORKSPACE_INPUT: WorkspaceLayoutInput = {
  availableWidth: 1280,
  mainMinWidth: 320,
  projects: { present: true, preferredWidth: 220, minWidth: 160, maxWidth: 360, collapsed: false, collapsedWidth: 56 },
  files: { present: false, preferredWidth: 220, minWidth: 220, maxWidth: 480 },
  auxiliary: { present: false, open: false, preferredWidth: 450, minWidth: 300, maxWidth: 800 },
  inspector: { present: false, open: false, preferredWidth: 450, minWidth: 300, maxWidth: 800 },
  segments: null,
};

const size = (value: number) => Number.isFinite(value) ? Math.max(0, value) : 0;
const clamp = (value: number, min: number, max: number) => Math.min(Math.max(size(value), min), Math.max(min, max));

export function resolveWorkspaceLayout(input: WorkspaceLayoutInput): WorkspaceLayout {
  const width = size(input.availableWidth);
  const mainMin = size(input.mainMinWidth);
  const p = input.projects;
  const f = input.files;
  const a = input.auxiliary;
  const i = input.inspector;
  const auxiliaryOpen = a.present && a.open;
  const inspectorOpen = i.present && i.open;
  const projectPreferred = p.collapsed ? p.collapsedWidth : clamp(p.preferredWidth, p.minWidth, p.maxWidth);
  const filesPreferred = clamp(f.preferredWidth, f.minWidth, f.maxWidth);
  const auxiliaryPreferred = clamp(a.preferredWidth, a.minWidth, a.maxWidth);
  const inspectorPreferred = clamp(i.preferredWidth, i.minWidth, i.maxWidth);
  const segments = auxiliaryOpen && input.segments && input.segments.first > 0 && input.segments.second > 0
    ? input.segments : null;
  const horizontal = segments?.orientation === 'horizontal';
  const vertical = segments?.orientation === 'vertical';
  let projectsDocked = p.present && !segments;
  let filesDocked = f.present;
  let auxiliaryDocked = auxiliaryOpen;
  let inspectorDocked = inspectorOpen;
  const budget = horizontal ? segments.first : width;
  const reservedAuxiliary = () => auxiliaryDocked && !segments ? a.minWidth : 0;
  const reservedInspector = () => inspectorDocked ? i.minWidth : 0;
  const minimum = () => mainMin + (projectsDocked ? projectPreferred : 0)
    + (filesDocked ? f.minWidth : 0) + reservedAuxiliary() + reservedInspector();

  // Presentations change only when all docked minima cannot fit. Projects
  // yield before Files; the document/Agent pair is retained as long as usable.
  if (minimum() > budget) projectsDocked = false;
  if (minimum() > budget) filesDocked = false;
  if (minimum() > budget) inspectorDocked = false;
  if (minimum() > budget && !segments) auxiliaryDocked = false;

  const projectWidth = projectsDocked ? projectPreferred : 0;
  let filesWidth = filesDocked ? filesPreferred : 0;
  let auxiliaryWidth = auxiliaryDocked && !segments ? auxiliaryPreferred : 0;
  let inspectorWidth = inspectorDocked ? inspectorPreferred : 0;
  let deficit = Math.max(0, mainMin - (budget - projectWidth - filesWidth - auxiliaryWidth - inspectorWidth));
  if (auxiliaryDocked && !segments) {
    const reclaimed = Math.min(deficit, auxiliaryWidth - a.minWidth);
    auxiliaryWidth -= reclaimed;
    deficit -= reclaimed;
  }
  if (inspectorDocked) {
    const reclaimed = Math.min(deficit, inspectorWidth - i.minWidth);
    inspectorWidth -= reclaimed;
    deficit -= reclaimed;
  }
  if (filesDocked) filesWidth -= Math.min(deficit, filesWidth - f.minWidth);

  const projectBodyWidth = Math.max(0, budget - projectWidth - auxiliaryWidth - inspectorWidth);
  const mainWidth = Math.max(0, projectBodyWidth - filesWidth);
  // Closed regions still need a useful retained content width, but reserve 0.
  const auxiliaryOverlay = !segments && (auxiliaryOpen ? !auxiliaryDocked : width < mainMin + a.minWidth);
  const auxiliaryRendered = horizontal ? segments.second : vertical ? width
    : auxiliaryOverlay ? Math.max(0, Math.min(420, width - 32))
    : auxiliaryOpen ? auxiliaryWidth : auxiliaryPreferred;

  return {
    availableWidth: width,
    mainWidth,
    projects: {
      presentation: !p.present ? 'absent' : projectsDocked ? 'docked' : 'overlay',
      width: !p.present ? 0 : projectsDocked ? projectWidth : Math.max(0, Math.min(360, width - 40)),
      resizeMax: clamp(width - mainMin - (filesDocked ? f.minWidth : 0) - reservedAuxiliary() - reservedInspector(), p.minWidth, p.maxWidth),
    },
    files: {
      presentation: !f.present ? 'absent' : filesDocked ? 'docked' : 'overlay',
      width: !f.present ? 0 : filesDocked ? filesWidth : Math.max(0, Math.min(360, projectBodyWidth - 40)),
      resizeMax: clamp(budget - projectWidth - mainMin - reservedAuxiliary() - reservedInspector(), f.minWidth, f.maxWidth),
    },
    auxiliary: {
      presentation: auxiliaryOverlay ? 'overlay' : 'docked',
      width: auxiliaryRendered,
      resizeMax: clamp(width - projectWidth - filesWidth - inspectorWidth - mainMin, a.minWidth, a.maxWidth),
    },
    inspector: {
      presentation: inspectorOpen && !inspectorDocked ? 'overlay' : 'docked',
      width: inspectorOpen ? inspectorDocked ? inspectorWidth : Math.max(0, budget - projectWidth - auxiliaryWidth) : inspectorPreferred,
      resizeMax: clamp(budget - projectWidth - filesWidth - auxiliaryWidth - mainMin, i.minWidth, i.maxWidth),
    },
    overlayOwner: auxiliaryOpen && auxiliaryOverlay ? 'auxiliary' : inspectorOpen && !inspectorDocked ? 'inspector' : null,
    sharedHeader: !projectsDocked || auxiliaryOverlay || Boolean(segments),
    segments,
  };
}
