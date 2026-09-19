'use client';

import { useEffect } from 'react';
import { confirmEditorNavigation } from '@/features/files/navigationGuard';

export function useEditorSaveGuards({
  dirty,
  save,
  keyboardEnabled = true,
}: {
  dirty: boolean;
  save: () => void | Promise<void>;
  keyboardEnabled?: boolean;
}) {
  useEffect(() => {
    if (!keyboardEnabled) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key !== 's' && event.key !== 'S') return;
      if (!dirty) return;
      event.preventDefault();
      void save();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [dirty, keyboardEnabled, save]);

  useEffect(() => {
    if (!dirty) return;
    const onNavigate = (event: MouseEvent) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = (event.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!link || link.download || (link.target && link.target !== '_self')) return;
      const target = new URL(link.href, window.location.href);
      if (target.pathname === window.location.pathname && target.search === window.location.search) return;
      if (!confirmEditorNavigation(true)) { event.preventDefault(); event.stopPropagation(); }
    };
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    document.addEventListener('click', onNavigate, true);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      document.removeEventListener('click', onNavigate, true);
    };
  }, [dirty]);
}
