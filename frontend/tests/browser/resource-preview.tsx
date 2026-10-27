import '@/app/responsive-workspace.css';
import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ThemeProvider } from '@/components/theme/ThemeProvider';
import { WorkspaceReference } from './resource-preview.reference';
import { ResourcePreview } from '@/features/resource-preview/ResourcePreview';
import { installNavigation, projectId, requests } from './resource-preview.ports';
import '@/app/globals.css';
import '@/shared-ui/src/styles/editor.css';

function Fixture() {
  const [path, setPath] = useState(new URLSearchParams(window.location.search).get('file') ?? '');
  window.onpopstate = () => setPath(decodeURIComponent(window.location.pathname.slice(projectId.length + 2)).replace(/\/$/, ''));
  installNavigation(setPath);
  return <ResourcePreview projectId={projectId} path={path} />;
}
const theme = new URLSearchParams(window.location.search).get('theme') ?? 'dark';
const reference = new URLSearchParams(window.location.search).has('reference');
const file = new URLSearchParams(window.location.search).get('file') ?? 'Welcome.md';

Object.assign(window, { resourcePreviewFixture: { requests } });
createRoot(document.getElementById('resource-preview-host')!).render(<StrictMode><ThemeProvider storageKey="resource-preview-fixture-theme" defaultTheme={theme} forcedTheme={theme} enableSystem={false}>{reference ? <WorkspaceReference file={file} /> : <Fixture />}</ThemeProvider></StrictMode>);
