import { buildDiffPreview, type DiffRequest } from './diffModel';

self.onmessage = (event: MessageEvent<{ id: number; request: DiffRequest }>) => {
  const { id, request } = event.data;
  try { self.postMessage({ id, result: buildDiffPreview(request) }); }
  catch { self.postMessage({ id, error: 'Unable to build diff preview' }); }
};
