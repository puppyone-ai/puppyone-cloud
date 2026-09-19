import { buildDiffPreview, type DiffPreview, type DiffRequest } from './diffModel';

type Job = { id: number; request: DiffRequest; done: (result: DiffPreview) => void };
const jobs: Job[] = [];
let active: Job | undefined;
let worker: Worker | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;
let sequence = 0;
const BUSY: DiffPreview = { lines: null, placeholder: 'Diff preview busy. Collapse and reopen this file to retry.' };

function finish(result: DiffPreview) {
  clearTimeout(timer);
  const completed = active;
  active = undefined;
  completed?.done(result);
  pump();
}

function pump() {
  if (active) return;
  active = jobs.shift();
  if (!active) { worker?.terminate(); worker = undefined; return; }
  const job = active;
  // An isolated, bounded fallback also supports older/test browsers. Work is
  // never performed during React render, nor allowed an unbounded LCS matrix.
  if (typeof Worker === 'undefined') {
    timer = setTimeout(() => {
      try { finish(buildDiffPreview(job.request)); }
      catch { finish({ lines: null, placeholder: 'Unable to build diff preview' }); }
    }, 0);
    return;
  }
  try {
    worker ??= new Worker(new URL('./diff.worker.ts', import.meta.url));
    worker.onmessage = event => {
      if (event.data.id !== active?.id) return;
      finish(event.data.result ?? { lines: null, placeholder: event.data.error });
    };
    worker.onerror = () => { worker?.terminate(); worker = undefined; finish({ lines: null, placeholder: 'Unable to build diff preview' }); };
    timer = setTimeout(() => { worker?.terminate(); worker = undefined; finish(BUSY); }, 5000);
    worker.postMessage({ id: job.id, request: job.request });
  } catch {
    worker?.terminate(); worker = undefined;
    finish({ lines: null, placeholder: 'Unable to start diff preview' });
  }
}

export function scheduleDiff(request: DiffRequest, done: Job['done']) {
  if (jobs.length >= 4) { done(BUSY); return () => {}; }
  const job = { id: ++sequence, request, done };
  jobs.push(job); pump();
  return () => {
    const index = jobs.indexOf(job);
    if (index >= 0) jobs.splice(index, 1);
    if (active === job) {
      clearTimeout(timer); worker?.terminate(); worker = undefined; active = undefined; pump();
    }
  };
}
