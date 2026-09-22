'use client';

import { useEffect, useRef, type ReactNode } from 'react';
import { DialogHeader, DialogRoot, DialogSurface } from '@/components/ui/Dialog';

const FOCUSABLE = 'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

/** Modal chrome stays mounted while settings data loads or a child dialog opens. */
export function ProjectSettingsDialogFrame({ children, projectName, onClose, onEscape, nestedDialogOpen = false }: {
  children: ReactNode;
  projectName?: string;
  onClose: () => void;
  onEscape?: () => void;
  nestedDialogOpen?: boolean;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const escapeRef = useRef(onEscape ?? onClose);
  escapeRef.current = onEscape ?? onClose;

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    frameRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const handleKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        escapeRef.current();
      } else if (event.key === 'Tab') {
        // Child edit/confirmation dialogs also render into the body. Keep
        // keyboard navigation in the top dialog while they are open.
        const dialogs = document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]');
        const topDialog = dialogs[dialogs.length - 1];
        if (!topDialog) return;
        const controls = Array.from(topDialog.querySelectorAll<HTMLElement>(FOCUSABLE))
          .filter(element => element.getClientRects().length > 0);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (!first) { event.preventDefault(); return; }
        if (!topDialog.contains(document.activeElement) || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        }
      }
    };
    document.addEventListener('keydown', handleKey, true);
    return () => {
      document.removeEventListener('keydown', handleKey, true);
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);

  useEffect(() => {
    if (!nestedDialogOpen && !frameRef.current?.contains(document.activeElement)) {
      frameRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    }
  }, [nestedDialogOpen]);

  return (
    <DialogRoot onClose={onClose} dismissOnBackdrop={!nestedDialogOpen}>
      <div ref={frameRef} style={{ display: 'contents' }}>
        <DialogSurface variant='project-settings' width={820} ariaLabel='Project settings' style={{ height: 'min(680px, calc(100dvh - 48px))', background: 'var(--po-canvas)' }}>
          <DialogHeader
            title='Project settings'
            description={projectName}
            onClose={onClose}
            closeTitle='Close project settings'
            style={{ padding: '16px 24px', borderBottom: '1px solid var(--po-divider)', background: 'var(--po-header)' }}
          />
          <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            {children}
          </div>
        </DialogSurface>
      </div>
    </DialogRoot>
  );
}
