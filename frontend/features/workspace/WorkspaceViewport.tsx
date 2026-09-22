'use client';

import { useEffect } from 'react';

/** Keyboard/browser-chrome adapter. No React layout state or breakpoint
 * dependencies: folding never replaces the root CSS box. */
export function WorkspaceViewport() {
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return; // 100dvh is the native fallback.
    const root = document.documentElement;
    let frame = 0;
    let height = '';
    let top = '';
    const update = () => {
      frame = 0;
      if (Math.abs(viewport.scale - 1) > 0.01) return; // Keep native pinch zoom.
      const nextHeight = `${viewport.height}px`;
      const nextTop = `${viewport.offsetTop}px`;
      if (height !== nextHeight) root.style.setProperty('--workspace-viewport-height', height = nextHeight);
      if (top !== nextTop) root.style.setProperty('--workspace-viewport-top', top = nextTop);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    update();
    viewport.addEventListener('resize', schedule);
    viewport.addEventListener('scroll', schedule);
    return () => {
      cancelAnimationFrame(frame);
      viewport.removeEventListener('resize', schedule);
      viewport.removeEventListener('scroll', schedule);
      root.style.removeProperty('--workspace-viewport-height');
      root.style.removeProperty('--workspace-viewport-top');
    };
  }, []);
  return null;
}
