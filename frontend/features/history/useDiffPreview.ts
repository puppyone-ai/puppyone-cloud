'use client';

import { useEffect, useState } from 'react';
import { scheduleDiff } from './diffScheduler';
import type { DiffRequest, DiffPreview } from './diffModel';

// Fixed-size LRU: SWR itself may retain many immutable response objects, so
// weak keys alone would not bound the number of derived previews we retain.
const cache: { request: DiffRequest; result: DiffPreview }[] = [];
export function useDiffPreview(request: DiffRequest | null) {
  const [snapshot, setSnapshot] = useState<{ request: DiffRequest; result: DiffPreview } | null>(null);
  useEffect(() => {
    if (!request) { setSnapshot(null); return; }
    const index = cache.findIndex(entry => entry.request.current === request.current
      && entry.request.previous === request.previous && entry.request.op === request.op);
    if (index >= 0) {
      const [cached] = cache.splice(index, 1); cache.push(cached);
      setSnapshot({ request, result: cached.result }); return;
    }
    return scheduleDiff(request, result => {
      // Only valid previews are immutable; failures/busy responses can retry.
      if (result.lines) { cache.push({ request, result }); if (cache.length > 8) cache.shift(); }
      setSnapshot({ request, result });
    });
  }, [request]);
  return request && snapshot?.request === request ? snapshot.result : null;
}
