'use client';

import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { useWorkspaceLayoutStore } from './layoutStore';
import { useStore } from 'zustand';

export type PaneMotion = 'idle' | 'opening' | 'closing';
export const PANE_MOTION_MS = 200;
const query = '(prefers-reduced-motion: reduce)';
const reducedMotion = () => typeof window.matchMedia === 'function' && window.matchMedia(query).matches;
const subscribe = (notify: () => void) => {
  const media = window.matchMedia?.(query);
  media?.addEventListener('change', notify);
  return () => media?.removeEventListener('change', notify);
};

/** Animate explicit intent only. Passive geometry changes settle immediately.
 * A closing region can retain its allocation until its exit finishes, keeping
 * its Header/overlay placement stable without retaining interactive state. */
export function usePaneMotion(expanded: boolean, presentationKey = '') {
  const environment = useStore(useWorkspaceLayoutStore(), state =>
    `${state.input.availableWidth}:${JSON.stringify(state.input.segments)}`);
  const reduce = useSyncExternalStore(subscribe, reducedMotion, () => false);
  const key = `${environment}:${presentationKey}:${reduce}`;
  const [state, setState] = useState({ expanded, key, phase: 'idle' as PaneMotion });
  if (state.expanded !== expanded || state.key !== key) {
    setState({ expanded, key, phase: state.key !== key || reduce ? 'idle' : expanded ? 'opening' : 'closing' });
  }
  const finish = useCallback(() => setState(current => current.phase === 'idle' ? current : { ...current, phase: 'idle' }), []);
  useEffect(() => {
    if (state.phase === 'idle') return;
    // Covers zero-distance transitions, background tabs and interrupted CSS.
    const timer = window.setTimeout(finish, PANE_MOTION_MS + 32);
    return () => window.clearTimeout(timer);
  }, [state.phase, state.expanded, state.key, finish]);
  return { phase: state.phase, present: expanded || state.phase === 'closing', finish };
}
