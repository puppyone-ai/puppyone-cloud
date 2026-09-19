import type { FileVersionDetail } from '@/lib/contentTreeApi';

export type DiffLineKind = 'add' | 'remove' | 'context' | 'hunk';
export interface DiffLine {
  kind: DiffLineKind;
  text: string;
  oldLine?: number;
  newLine?: number;
}

export const DIFF_MAX_ROWS = 600;
const DIFF_MAX_LINES = 4000;
const DIFF_MAX_CELLS = 1_000_000;
const DIFF_MAX_TEXT = 256_000;

function lineDiff(a: string[], b: string[]): DiffLine[] {
  if (a.length + b.length > DIFF_MAX_LINES || (a.length + 1) * (b.length + 1) > DIFF_MAX_CELLS) {
    return [
      { kind: 'hunk', text: '@@ Large change: simplified preview @@' },
      ...a.slice(0, DIFF_MAX_ROWS / 2).map((text) => ({ kind: 'remove' as const, text })),
      ...b.slice(0, DIFF_MAX_ROWS / 2).map((text) => ({ kind: 'add' as const, text })),
    ];
  }

  const m = a.length;
  const n = b.length;
  const dp: Uint16Array = new Uint16Array((m + 1) * (n + 1));
  const idx = (i: number, j: number) => i * (n + 1) + j;

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[idx(i, j)] =
        a[i - 1] === b[j - 1]
          ? dp[idx(i - 1, j - 1)] + 1
          : Math.max(dp[idx(i - 1, j)], dp[idx(i, j - 1)]);
    }
  }

  const out: DiffLine[] = [];
  let i = m;
  let j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
      out.push({ kind: 'context', text: a[i - 1] });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[idx(i, j - 1)] >= dp[idx(i - 1, j)])) {
      out.push({ kind: 'add', text: b[j - 1] });
      j--;
    } else {
      out.push({ kind: 'remove', text: a[i - 1] });
      i--;
    }
  }
  out.reverse();
  return out;
}

function addLineNumbers(lines: DiffLine[]): DiffLine[] {
  let oldLine = 1;
  let newLine = 1;
  return lines.map((line) => {
    if (line.kind === 'remove') {
      return { ...line, oldLine: oldLine++ };
    }
    if (line.kind === 'add') {
      return { ...line, newLine: newLine++ };
    }
    if (line.kind === 'context') {
      return { ...line, oldLine: oldLine++, newLine: newLine++ };
    }
    return line;
  });
}

function compactDiffLines(lines: DiffLine[], contextRadius = 3): DiffLine[] {
  const changedIndexes = lines
    .map((line, index) => (line.kind === 'add' || line.kind === 'remove' ? index : -1))
    .filter((index) => index >= 0);

  if (changedIndexes.length === 0) return lines;

  const ranges: Array<{ start: number; end: number }> = [];
  for (const index of changedIndexes) {
    const start = Math.max(0, index - contextRadius);
    const end = Math.min(lines.length - 1, index + contextRadius);
    const last = ranges[ranges.length - 1];
    if (last && start <= last.end + 1) {
      last.end = Math.max(last.end, end);
    } else {
      ranges.push({ start, end });
    }
  }

  const compacted: DiffLine[] = [];
  let cursor = 0;
  for (const range of ranges) {
    const hiddenCount = range.start - cursor;
    if (hiddenCount > 0) {
      compacted.push({
        kind: 'hunk',
        text: hiddenCount === 1 ? '@@ 1 unchanged line @@' : `@@ ${hiddenCount} unchanged lines @@`,
      });
    }
    compacted.push(...lines.slice(range.start, range.end + 1));
    cursor = range.end + 1;
  }

  const trailingHidden = lines.length - cursor;
  if (trailingHidden > 0) {
    compacted.push({
      kind: 'hunk',
      text: trailingHidden === 1 ? '@@ 1 unchanged line @@' : `@@ ${trailingHidden} unchanged lines @@`,
    });
  }

  return compacted;
}

// Pull lines out of the commit-content response. Backend returns
// either `content_text` (raw decoded text — markdown / yaml / source)
// or `content` (already-parsed JSON for JSON files). The earlier
// version of this fn looked at `content_json`, which the endpoint
// never returns — so JSON-file diffs always silently fell through to
// the "Binary file" placeholder. Keep both fallbacks so the function
// stays robust if the wire shape ever shifts.
function fileToLines(detail: FileVersionDetail): string[] | null {
  if (detail.is_binary) {
    return null;
  }
  if (detail.content_text != null) {
    return detail.content_text.split('\n');
  }
  if (detail.content != null) {
    try {
      const text = JSON.stringify(detail.content, null, 2);
      if (text.length > DIFF_MAX_TEXT) return null;
      return text.split('\n');
    } catch {
      return null;
    }
  }
  return null;
}


export interface DiffRequest {
  op: string;
  current?: FileVersionDetail;
  previous?: FileVersionDetail;
}
export interface DiffPreview { lines: DiffLine[] | null; placeholder: string | null }

export function buildDiffPreview({ op, current, previous }: DiffRequest): DiffPreview {
  if ([current, previous].some(detail => detail?.truncated || (detail?.size_bytes ?? 0) > DIFF_MAX_TEXT || (detail?.content_text?.length ?? 0) > DIFF_MAX_TEXT)) {
    return { lines: null, placeholder: 'Large file: open the version to inspect its full content.' };
  }
  const before = previous ? fileToLines(previous) : null;
  const after = current ? fileToLines(current) : null;
  let lines: DiffLine[];
  if (op === 'added' && after) lines = after.slice(0, DIFF_MAX_ROWS + 1).map((text, index) => ({ kind: 'add', text, newLine: index + 1 }));
  else if (op === 'deleted' && before) lines = before.slice(0, DIFF_MAX_ROWS + 1).map((text, index) => ({ kind: 'remove', text, oldLine: index + 1 }));
  else if (op !== 'added' && op !== 'deleted' && before && after) lines = compactDiffLines(addLineNumbers(lineDiff(before, after)));
  else return { lines: null, placeholder: previous || op !== 'deleted' ? 'Binary file, oversized formatted JSON, or unchanged metadata' : 'No previous version available' };
  let remaining = 64_000;
  const result: DiffLine[] = [];
  let truncated = lines.length > DIFF_MAX_ROWS;
  for (const line of lines.slice(0, DIFF_MAX_ROWS)) {
    if (remaining <= 0) { truncated = true; break; }
    const maxLength = Math.min(2000, remaining);
    const text = line.text.length > maxLength ? line.text.slice(0, maxLength) + ' …' : line.text;
    truncated ||= text !== line.text;
    result.push({ ...line, text });
    remaining -= text.length;
  }
  if (truncated) result.push({ kind: 'hunk', text: '@@ Preview truncated — open the version for full content @@' });
  return { lines: result, placeholder: null };
}
