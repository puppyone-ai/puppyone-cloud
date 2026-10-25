import { createRoot } from 'react-dom/client';
import { AccessManagement } from '@/features/access/lifecycle/AccessManagement';
import '@/app/globals.css';

createRoot(document.getElementById('root')!).render(
  <main style={{ minHeight: '100dvh', background: 'var(--po-canvas)', padding: 24, display: 'flex', justifyContent: 'center' }}>
    <aside style={{ width: 420, maxWidth: '100%', height: 'calc(100dvh - 48px)', overflowY: 'auto', border: '1px solid var(--po-border)', background: 'var(--po-panel)', borderRadius: 10 }}>
      <header style={{ borderBottom: '1px solid var(--po-border-subtle)', color: 'var(--po-text)', fontSize: 14, fontWeight: 600, padding: '14px 20px' }}>Agent access</header>
      <AccessManagement projectId="fixture-project" />
    </aside>
  </main>,
);
