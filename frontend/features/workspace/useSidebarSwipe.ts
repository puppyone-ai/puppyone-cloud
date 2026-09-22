'use client';

import { useEffect, useRef, type RefObject } from 'react';

/** Touch-only dismissal. Vertical scrolling, pinch zoom and text editing stay native. */
export function useSidebarSwipe(ref: RefObject<HTMLElement | null>, enabled: boolean, side: 'left' | 'right', onClose: () => void) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const panel = ref.current;
    if (!enabled || !panel) return;
    let gesture: { id: number; x: number; y: number; locked: boolean; distance: number } | null = null;
    let suppressClick = false;
    const direction = side === 'left' ? -1 : 1;
    const reset = () => {
      const id = gesture?.id;
      gesture = null;
      panel.style.removeProperty('--sidebar-drag-x');
      panel.removeAttribute('data-swiping');
      if (id !== undefined && panel.hasPointerCapture?.(id)) panel.releasePointerCapture(id);
    };
    const down = (event: PointerEvent) => {
      suppressClick = false;
      if (event.isPrimary === false) { reset(); return; }
      if (event.pointerType !== 'touch' && event.pointerType !== 'pen') return;
      const target = event.target instanceof Element ? event.target : null;
      if (!target?.closest('[data-sidebar-swipe-surface]') || target.closest('input, textarea, select, [contenteditable="true"], pre, [role="slider"]')) return;
      gesture = { id: event.pointerId, x: event.clientX, y: event.clientY, locked: false, distance: 0 };
    };
    const move = (event: PointerEvent) => {
      if (!gesture || event.pointerId !== gesture.id) return;
      const dx = (event.clientX - gesture.x) * direction;
      const dy = Math.abs(event.clientY - gesture.y);
      if (!gesture.locked) {
        if (dy > 10 && dy >= Math.abs(dx)) { reset(); return; }
        if (dx < 10 || dx < dy * 1.25) return;
        gesture.locked = true;
        suppressClick = true;
        panel.setPointerCapture?.(event.pointerId);
        panel.setAttribute('data-swiping', 'true');
      }
      event.preventDefault();
      gesture.distance = Math.max(0, dx);
      panel.style.setProperty('--sidebar-drag-x', `${gesture.distance * direction}px`);
    };
    const up = (event: PointerEvent) => {
      if (!gesture || event.pointerId !== gesture.id) return;
      const dismiss = gesture.locked && gesture.distance >= 56;
      reset();
      if (dismiss) closeRef.current();
    };
    const click = (event: MouseEvent) => {
      if (!suppressClick) return;
      suppressClick = false;
      event.preventDefault();
      event.stopPropagation();
    };
    // Moving implicit touch capture from a child to the panel emits a bubbled
    // lostpointercapture on that child. Only losing the panel's capture cancels.
    const lostCapture = (event: PointerEvent) => { if (event.target === panel) reset(); };
    panel.addEventListener('pointerdown', down);
    panel.addEventListener('pointermove', move, { passive: false });
    panel.addEventListener('pointerup', up);
    panel.addEventListener('pointercancel', reset);
    panel.addEventListener('lostpointercapture', lostCapture);
    panel.addEventListener('click', click, true);
    return () => {
      panel.removeEventListener('pointerdown', down);
      panel.removeEventListener('pointermove', move);
      panel.removeEventListener('pointerup', up);
      panel.removeEventListener('pointercancel', reset);
      panel.removeEventListener('lostpointercapture', lostCapture);
      panel.removeEventListener('click', click, true);
      reset();
    };
  }, [enabled, ref, side]);
}
