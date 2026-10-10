import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ResourcePreview } from '@/features/resource-preview/ResourcePreview';
import { installNavigation, projectId, requests } from './resource-preview.ports';
import '@/app/globals.css';

function Fixture() {
  const [path, setPath] = useState('');
  installNavigation(setPath);
  return <ResourcePreview projectId={projectId} path={path} />;
}
window.history.replaceState({}, '', `/${projectId}/`);
Object.assign(window, { resourcePreviewFixture: { requests } });
createRoot(document.getElementById('resource-preview-host')!).render(<StrictMode><Fixture /></StrictMode>);
