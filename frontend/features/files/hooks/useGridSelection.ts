'use client';

/**
 * useGridSelection — multi-select state for the Data folder view.
 *
 * Mirrors Finder/Explorer conventions:
 *   - Plain click   → clear selection + open the item.
 *   - Cmd/Ctrl+click → toggle the item in/out of the selection.
 *   - Shift+click    → range-select between the last anchor and the
 *                      clicked item, using the on-screen item order.
 *   - Click empty    → clear.
 *   - Esc            → clear.
 *
 * Selection state is bounded to the items currently visible: when
 * the displayed list changes (folder navigation, refresh), any ids
 * no longer present are dropped so a stale selection can't trigger
 * a delete on a sibling that isn't there anymore.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

interface UseGridSelectionOptions {
  /** The currently visible item ids in their on-screen order. Range
   *  selection uses this list to pick the inclusive slice. */
  orderedIds: string[];
}

export interface GridSelectionApi {
  selectedIds: Set<string>;
  selectedCount: number;
  isSelected: (id: string) => boolean;
  /** Toggle membership without changing other ids (Cmd/Ctrl click). */
  toggle: (id: string) => void;
  /** Inclusive range from anchor → id; if no anchor, treats as toggle. */
  selectRangeTo: (id: string) => void;
  /** Replace the whole selection with a single id (used as the anchor
   *  for the next shift-click). */
  selectOnly: (id: string) => void;
  clear: () => void;
  selectAll: () => void;
  /** Selected item paths in display order — useful for callers
   *  that want stable, scopeable inputs (e.g. bulk delete). */
  selectedInOrder: string[];
}

export function useGridSelection({
  orderedIds,
}: UseGridSelectionOptions): GridSelectionApi {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const anchorRef = useRef<string | null>(null);

  // Drop ids that left the current view. Without this, navigating
  // back to a folder whose previously-selected items are gone would
  // leave a phantom selection that bulk-delete then tries to operate
  // on, returning a 404 from the server.
  useEffect(() => {
    if (selectedIds.size === 0) return;
    const live = new Set(orderedIds);
    let changed = false;
    const next = new Set<string>();
    selectedIds.forEach((id) => {
      if (live.has(id)) next.add(id);
      else changed = true;
    });
    if (changed) {
      setSelectedIds(next);
      if (anchorRef.current && !live.has(anchorRef.current)) {
        anchorRef.current = null;
      }
    }
  }, [orderedIds, selectedIds]);

  const isSelected = useCallback(
    (id: string) => selectedIds.has(id),
    [selectedIds],
  );

  const toggle = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    anchorRef.current = id;
  }, []);

  const selectRangeTo = useCallback(
    (id: string) => {
      const anchor = anchorRef.current;
      if (!anchor || anchor === id) {
        // No anchor → treat first shift-click as a single-select.
        setSelectedIds(new Set([id]));
        anchorRef.current = id;
        return;
      }
      const startIdx = orderedIds.indexOf(anchor);
      const endIdx = orderedIds.indexOf(id);
      if (startIdx === -1 || endIdx === -1) {
        setSelectedIds(new Set([id]));
        anchorRef.current = id;
        return;
      }
      const [lo, hi] =
        startIdx <= endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
      const slice = orderedIds.slice(lo, hi + 1);
      setSelectedIds(new Set(slice));
      // Anchor stays put — repeated shift-clicks pivot off the
      // original anchor (matches Finder).
    },
    [orderedIds],
  );

  const selectOnly = useCallback((id: string) => {
    setSelectedIds(new Set([id]));
    anchorRef.current = id;
  }, []);

  const clear = useCallback(() => {
    setSelectedIds(new Set());
    anchorRef.current = null;
  }, []);

  const selectAll = useCallback(() => {
    setSelectedIds(new Set(orderedIds));
    anchorRef.current = orderedIds[0] ?? null;
  }, [orderedIds]);

  const selectedInOrder = useMemo(() => {
    if (selectedIds.size === 0) return [];
    return orderedIds.filter((id) => selectedIds.has(id));
  }, [orderedIds, selectedIds]);

  return {
    selectedIds,
    selectedCount: selectedIds.size,
    isSelected,
    toggle,
    selectRangeTo,
    selectOnly,
    clear,
    selectAll,
    selectedInOrder,
  };
}
