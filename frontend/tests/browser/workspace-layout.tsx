import { useEffect, useState, type CSSProperties } from 'react';
import { createRoot } from 'react-dom/client';
import { ResponsiveWorkspaceProvider, useWorkspaceActions, useWorkspaceNavigation } from '@/features/workspace/responsive';
import { WorkspaceLayoutFrame } from '@/features/workspace/WorkspaceLayoutFrame';
import { createWorkspaceLayoutStore, useWorkspacePane } from '@/features/workspace/layoutStore';
import { useWorkspaceRegions } from '@/features/workspace/regions';
import { ResponsiveDrawer } from '@/components/sidebar/ResponsiveDrawer';
import { FileNavigationRegion } from '@/components/sidebar/FileNavigationRegion';
import { WorkspaceFileColumn } from '@/components/sidebar/WorkspaceFileColumn';
import { WorkspaceAuxiliaryRegion } from '@/components/sidebar/WorkspaceAuxiliaryRegion';
import { WorkspaceInspectorRegion } from '@/components/sidebar/WorkspaceInspectorRegion';
import { WorkspaceFilesButton } from '@/components/sidebar/WorkspaceFilesButton';
import { WorkspaceNavigationButton } from '@/components/sidebar/WorkspaceNavigationButton';
import '@/app/globals.css';
import '@/app/responsive-workspace.css';

const store = createWorkspaceLayoutStore();
const renders = { editor: 0, chat: 0, mounts: 0, unmounts: 0 };
const flex: CSSProperties = { display: 'flex', minWidth: 0, minHeight: 0, flex: 1 };
function Editor() {
  renders.editor++;
  useEffect(() => { renders.mounts++; return () => { renders.unmounts++; }; }, []);
  return <textarea aria-label='Editor draft' defaultValue='Unsaved editor draft' style={{ ...flex, width: '100%' }} />;
}
function Chat() {
  renders.chat++;
  useEffect(() => { renders.mounts++; return () => { renders.unmounts++; }; }, []);
  return <><header data-sidebar-swipe-surface style={{ height: 46 }}>New chat</header><textarea aria-label='Chat draft' defaultValue='Unsaved chat draft' style={flex} /></>;
}
function Projects() {
  const [collapsed, setCollapsed] = useState(false);
  useWorkspacePane('projects', { present: true, preferredWidth: 220, minWidth: 160, maxWidth: 360, collapsed, collapsedWidth: 56 });
  const open = useWorkspaceNavigation(s => s.projectsOpen);
  const { setProjectsOpen } = useWorkspaceActions();
  return <ResponsiveDrawer id='workspace-projects' label='Projects' open={open} collapsed={collapsed} onClose={() => setProjectsOpen(false)}>
    <aside className='workspace-project-rail' style={{ width: '100%', borderRight: '1px solid var(--po-divider)' }}><button data-project-collapse onClick={() => setCollapsed(value => !value)}>Toggle projects rail</button></aside>
  </ResponsiveDrawer>;
}
function Session() {
  const regions = useWorkspaceRegions();
  const [visible, setVisible] = useState(true);
  const [inspector, setInspector] = useState(false);
  return <main className='workspace-main' ref={regions.appContent} style={{ ...flex, flexDirection: 'column', overflow: 'hidden' }}><div className='workspace-project-layout'>
    <header data-workspace-header ref={regions.header} style={{ display: 'flex', alignItems: 'center', minHeight: 46 }}><WorkspaceNavigationButton /><WorkspaceFilesButton /><span>Project / Files</span><button data-workspace-chat-trigger onClick={() => setVisible(v => !v)}>Toggle Agent</button><button data-inspector-trigger onClick={() => { setVisible(false); setInspector(v => !v); }}>Toggle Inspector</button></header>
    <div className='workspace-primary-body' ref={regions.projectBody} style={{ ...flex, overflow: 'hidden', position: 'relative' }}>
      <div className='workspace-data-content' style={{ ...flex, position: 'relative' }}>
        <FileNavigationRegion><WorkspaceFileColumn><nav>Files</nav></WorkspaceFileColumn></FileNavigationRegion>
        <div className='workspace-editor-viewport' ref={regions.editor} style={{ ...flex, overflow: 'hidden' }}><Editor /></div>
      </div>
      <WorkspaceInspectorRegion visible={inspector} onClose={() => setInspector(false)} background='var(--po-panel)'><button onClick={() => setInspector(false)}>Close inspector</button><p>File details</p></WorkspaceInspectorRegion>
    </div>
    <WorkspaceAuxiliaryRegion visible={visible} label='Agent sidebar' trigger='chat' onClose={() => setVisible(false)}><Chat /></WorkspaceAuxiliaryRegion>
  </div></main>;
}
const content = <ResponsiveWorkspaceProvider layoutStore={store}><WorkspaceLayoutFrame><Projects /><Session /></WorkspaceLayoutFrame></ResponsiveWorkspaceProvider>;
const settle = () => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
const settleMotion = async () => { await new Promise(resolve => setTimeout(resolve, 280)); await settle(); };
const rect = (selector: string) => document.querySelector<HTMLElement>(selector)!.getBoundingClientRect();
const equal = (actual: number, expected: number, label: string) => { if (Math.abs(actual - expected) > 0.6) throw new Error(`${label}: ${actual} != ${expected}`); };

async function verify() {
  const host = document.getElementById('workspace-test-host')!;
  const editor = document.querySelector('textarea[aria-label="Editor draft"]');
  const chat = document.querySelector('textarea[aria-label="Chat draft"]');
  const before = { ...renders };
  const rows: string[] = [];
  const widths = [1440, 1280, 1210, 1200, 1100, 1061, 1060, 1059, 1024, 1023, 960, 900, 899, 841, 840, 839, 820, 700, 621, 620, 619, 390, 320, 390, 820, 1280];
  for (const width of widths) {
    host.style.width = `${width}px`;
    await settle();
    const layout = store.getState().layout;
    const agent = rect('.workspace-auxiliary');
    const main = rect('.workspace-editor-viewport');
    equal(layout.availableWidth, width, 'Observed host width');
    equal(main.width, layout.mainWidth, 'Editor actual width');
    equal(agent.width, layout.auxiliary.width, 'Agent actual width');
    equal(rect('#workspace-files .workspace-drawer-sheet').width, layout.files.width, 'Files actual width');
    equal(rect('#workspace-projects .workspace-drawer-sheet').width, layout.projects.width, 'Projects actual width');
    if (main.width < Math.min(width, 320)) throw new Error('Editor fell below usable minimum');
    if (layout.sharedHeader) equal(agent.top, rect('[data-workspace-header]').bottom, 'Agent below Header');
    if (layout.auxiliary.presentation === 'overlay' && !document.querySelector('.workspace-primary-body[inert]')) throw new Error('Overlay did not isolate document');
    if (editor !== document.querySelector('textarea[aria-label="Editor draft"]') || chat !== document.querySelector('textarea[aria-label="Chat draft"]')) throw new Error('Content remounted');
    rows.push(`${width}: Projects ${layout.projects.presentation}, Files ${layout.files.presentation} ${layout.files.width}, Agent ${agent.width}, Editor ${main.width}`);
  }
  if (renders.editor !== before.editor || renders.chat !== before.chat || renders.unmounts !== before.unmounts) throw new Error('Pixel resize propagated into business content');
  // Exercise the actual keyboard resize handlers, then verify displayed widths
  // restore after compression. These are user actions, not direct store writes.
  document.querySelector('.workspace-auxiliary [aria-label="Resize panel"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
  document.querySelector('[aria-label="Resize sidebar"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  await settle();
  equal(rect('.workspace-auxiliary').width, 466, 'User Agent resize');
  equal(rect('#workspace-files .workspace-drawer-sheet').width, 236, 'User Files resize');
  host.style.width = '390px'; await settle();
  host.style.width = '1280px'; await settle();
  equal(rect('.workspace-auxiliary').width, 466, 'Agent preference restored');
  equal(rect('#workspace-files .workspace-drawer-sheet').width, 236, 'Files preference restored');
  document.querySelector<HTMLButtonElement>('[data-workspace-chat-trigger]')!.click(); await settleMotion();
  equal(rect('.workspace-auxiliary').width, 0, 'Closed Agent budget');
  equal(rect('.workspace-editor-viewport').width, 824, 'Released Agent space');
  document.querySelector<HTMLButtonElement>('[data-workspace-chat-trigger]')!.click(); await settleMotion();
  equal(rect('.workspace-auxiliary').width, 466, 'Reopened Agent preference');
  document.querySelector<HTMLButtonElement>('[data-inspector-trigger]')!.click(); await settleMotion();
  for (const width of [1280, 1100, 900, 840, 839, 620, 390, 1280]) {
    host.style.width = `${width}px`; await settle();
    const layout = store.getState().layout;
    equal(rect('.workspace-inspector').width, layout.inspector.width, 'Inspector actual width');
    equal(rect('.workspace-editor-viewport').width, layout.mainWidth, 'Inspector document budget');
    if (layout.mainWidth < 320) throw new Error('Inspector consumed the editor minimum');
    equal(rect('.workspace-inspector').top, rect('[data-workspace-header]').bottom, 'Inspector below Header');
  }
  return `PASS: ${widths.length} Agent widths + 8 inspector widths; real frame/region CSS geometry; minima; Header anchor; inert; retained DOM; zero editor/Chat resize renders; keyboard resizing; preference restore; close/reopen budget.\n\n${rows.join('\n')}`;
}

async function verifyMotion() {
  const host = document.getElementById('workspace-test-host')!;
  const rows: string[] = [];
  const click = (selector: string) => document.querySelector<HTMLButtonElement>(selector)!.click();
  const frames = async (selector: string, action: () => void) => {
    const panel = document.querySelector<HTMLElement>(selector)!;
    const samples: { width: number; x: number; painted: boolean; content: number }[] = [];
    action();
    const start = performance.now();
    while (performance.now() - start < 270) {
      await new Promise(requestAnimationFrame);
      const box = panel.getBoundingClientRect();
      samples.push({ width: box.width, x: box.x, painted: getComputedStyle(panel).visibility === 'visible', content: panel.querySelector('[data-panel-content]')?.getBoundingClientRect().width ?? 0 });
    }
    return samples;
  };
  if (store.getState().input.inspector.open) { click('[data-inspector-trigger]'); await settleMotion(); }
  if (!store.getState().input.auxiliary.open) { click('[data-workspace-chat-trigger]'); await settleMotion(); }
  for (const inspector of [false, true]) {
    const selector = inspector ? '.workspace-inspector' : '.workspace-auxiliary';
    const trigger = inspector ? '[data-inspector-trigger]' : '[data-workspace-chat-trigger]';
    if (inspector) { click(trigger); await settleMotion(); }
    for (const width of [1280, 390]) {
      host.style.width = `${width}px`; await settle();
      const original = rect(selector);
      const headerBottom = rect('[data-workspace-header]').bottom;
      const contentWidth = rect(`${selector} > [data-panel-content]`).width;
      const hiddenX = original.right;
      const closing = await frames(selector, () => click(trigger));
      const intermediate = closing.filter(frame => width === 390 ? frame.x > original.x + 1 && frame.x < hiddenX - 1 : frame.width > 1 && frame.width < original.width - 1);
      if (!intermediate.length || intermediate.some(frame => !frame.painted)) throw new Error(`${selector} ${width}: exit missing or hidden before completion`);
      for (const frame of intermediate) equal(frame.content, contentWidth, 'Exit content must not squeeze');
      if (width === 390) equal(rect(selector).top, headerBottom, 'Exit remains below Header');
      const opening = await frames(selector, () => click(trigger));
      const entering = opening.filter(frame => width === 390 ? frame.x > original.x + 1 && frame.x < hiddenX - 1 : frame.width > 1 && frame.width < original.width - 1);
      if (!entering.length) throw new Error(`${selector} ${width}: entry has no intermediate frames`);
      equal(rect(selector).width, original.width, 'Restored open width');
      rows.push(`${inspector ? 'Inspector' : 'Agent'} ${width}px: ${intermediate.length} exit + ${entering.length} entry frames; retained content width`);
    }
  }
  click('[data-inspector-trigger]'); await settleMotion();
  host.style.width = '1280px'; await settle();
  click('[data-workspace-chat-trigger]'); await settleMotion();
  // A quick reversal must finish open; resizing during an exit must settle now.
  click('[data-workspace-chat-trigger]'); await settle();
  click('[data-workspace-chat-trigger]'); await settleMotion();
  equal(rect('.workspace-auxiliary').width, store.getState().layout.auxiliary.width, 'Rapid reversal');
  click('[data-workspace-chat-trigger]'); await settle();
  host.style.width = '1100px'; await settle();
  if (document.querySelector('.workspace-auxiliary')!.getAttribute('data-motion') !== 'idle') throw new Error('Resize left a stale animation');
  equal(rect('.workspace-auxiliary').width, 0, 'Resize settles closing');
  host.style.width = '1280px'; await settle();
  for (const expanded of [false, true]) {
    const samples = await frames('#workspace-projects', () => click('[data-project-collapse]'));
    if (!samples.some(frame => frame.width > 57 && frame.width < 219)) throw new Error('Projects collapse/expand missing');
    equal(rect('#workspace-projects').width, expanded ? 220 : 56, 'Projects final width');
  }
  host.style.width = '390px'; await settle();
  for (const open of [true, false]) {
    const samples = await frames('#workspace-files .workspace-drawer-sheet', () => click(open ? '.workspace-files-button' : '[aria-label="Close files navigation"]'));
    if (!samples.some(frame => frame.x < -1 && frame.x > -350 && frame.painted)) throw new Error('Files drawer motion missing');
  }
  return `PASS: explicit open/close motion in real CSS, rapid reversal, immediate resize cancellation, Projects collapse/expand and Files drawer.\n${rows.join('\n')}`;
}
function Fixture() {
  const [result, setResult] = useState('Ready');
  useEffect(() => {
    if (new URLSearchParams(location.search).get('run') !== 'all') return;
    let active = true;
    void settleMotion().then(async () => {
      if (!active) return;
      setResult('Running geometry and motion checks');
      const geometry = await verify();
      const motion = await verifyMotion();
      if (active) setResult(`${motion}\n\n${geometry}`);
    }).catch(error => { if (active) setResult(`FAIL: ${error.message}`); });
    return () => { active = false; };
  }, []);
  return <><div style={{ padding: 12, height: 170, overflow: 'auto' }}><button onClick={() => { setResult('Running'); void verify().then(setResult, e => setResult(`FAIL: ${e.message}`)); }}>Run browser geometry regression</button><button onClick={() => { setResult('Running motion checks'); void verifyMotion().then(setResult, e => setResult(`FAIL: ${e.message}`)); }}>Run sidebar motion regression</button><button onClick={() => { localStorage.removeItem('sidebar-width:explorer-sidebar:data'); location.reload(); }}>Reset fixture</button><pre role='status' style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{result}</pre></div><div id='workspace-test-host' style={{ width: 1280, height: 500, overflow: 'hidden' }}>{content}</div><style>{'.workspace-root { height: 500px !important; top: 0 !important; } body { overflow: auto; }'}</style></>;
}
if (new URLSearchParams(location.search).get('run') === 'all') localStorage.removeItem('sidebar-width:explorer-sidebar:data');
createRoot(document.getElementById('root')!).render(<Fixture />);
