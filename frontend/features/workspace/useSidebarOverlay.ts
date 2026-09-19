'use client';

import { useCallback, useEffect, useRef, type RefObject } from 'react';

const FOCUSABLE = 'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]';
const locks = new WeakMap<HTMLElement, { count: number; wasInert: boolean }>();
function lock(element: HTMLElement) {
  const entry = locks.get(element) ?? { count: 0, wasInert: element.hasAttribute('inert') };
  entry.count++;
  locks.set(element, entry);
  element.setAttribute('inert', '');
  return () => {
    if (--entry.count) return;
    if (!entry.wasInert) element.removeAttribute('inert');
    locks.delete(element);
  };
}

/** One owner for sidebar focus, Escape and explicit covered regions. Body
 * portals are intentionally outside those regions; menus/dialogs take priority. */
export function useSidebarOverlay({ active, modal = false, panel, covered, onClose, restoreFocus = true, returnFocus }: {
  active: boolean;
  restoreFocus?: boolean;
  returnFocus?: () => HTMLElement | null;
  modal?: boolean;
  panel: RefObject<HTMLElement>;
  covered: readonly RefObject<HTMLElement>[];
  onClose: () => void;
}) {
  const callback = useRef(onClose);
  callback.current = onClose;
  const previous = useRef<HTMLElement | null>(null);
  const focusTarget = useRef(returnFocus);
  focusTarget.current = returnFocus;
  const restore = useRef(restoreFocus);
  restore.current = restoreFocus;
  const close = useCallback(() => callback.current(), []);

  useEffect(() => {
    if (!active) return;
    const release = covered.flatMap(ref => ref.current ? [lock(ref.current)] : []);
    return () => release.forEach(unlock => unlock());
  }, [active, covered]);

  useEffect(() => {
    if (!active || !panel.current) return;
    previous.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!panel.current.contains(document.activeElement)) {
      const initial = panel.current.querySelector<HTMLElement>('[data-drawer-close], button[aria-label^="Close"]') ?? panel.current.querySelector<HTMLElement>(FOCUSABLE);
      initial?.focus({ preventScroll: true });
    }
    const keydown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      const dialogs = document.querySelectorAll('[role="dialog"][aria-modal="true"]');
      if (dialogs.length && dialogs[dialogs.length - 1] !== panel.current) return;
      const menus = Array.from(document.querySelectorAll<HTMLElement>('[role="menu"]')).filter(menu => menu.getClientRects().length);
      if (event.key === 'Escape') {
        if (menus.length) return;
        event.preventDefault();
        close();
      }
      if (event.key === 'Tab' && modal && panel.current) {
        const roots = [panel.current, ...menus.filter(menu => !panel.current!.contains(menu))];
        const controls = roots.flatMap(root => Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)))
          .filter(item => item.getClientRects().length && !item.closest('[inert]'));
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (!first) { event.preventDefault(); return; }
        const inside = roots.some(root => root.contains(document.activeElement));
        if (!inside || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        }
      }
    };
    document.addEventListener('keydown', keydown);
    return () => {
      document.removeEventListener('keydown', keydown);
      const target = focusTarget.current?.() ?? previous.current;
      if (restore.current && target?.isConnected && target.getClientRects().length && !target.closest('[inert], [aria-hidden="true"]')) target.focus({ preventScroll: true });
    };
  }, [active, modal, panel, close]);
  return close;
}
