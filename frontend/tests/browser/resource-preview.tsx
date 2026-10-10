import '@/app/responsive-workspace.css';
import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { WorkspaceReference } from './resource-preview.reference';
import { ResourcePreview } from '@/features/resource-preview/ResourcePreview';
import { installNavigation, projectId, requests } from './resource-preview.ports';
import '@/app/globals.css';
import '@/shared-ui/src/styles/editor.css';

function Fixture() {
  const [path, setPath] = useState('');
  window.onpopstate = () => setPath(decodeURIComponent(window.location.pathname.slice(projectId.length + 2)).replace(/\/$/, ''));
  installNavigation(setPath);
  return <ResourcePreview projectId={projectId} path={path} />;
}
const reference = new URLSearchParams(window.location.search).has('reference');
window.history.replaceState({}, '', `/${projectId}/`);
Object.assign(window, { resourcePreviewFixture: { requests } });
createRoot(document.getElementById('resource-preview-host')!).render(<StrictMode>{reference ? <WorkspaceReference /> : <Fixture />}</StrictMode>);
