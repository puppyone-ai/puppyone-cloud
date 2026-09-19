import { expect, it } from 'vitest';
import { buildDiffPreview, DIFF_MAX_ROWS } from '@/features/history/diffModel';
import type { FileVersionDetail } from '@/lib/contentTreeApi';
const file = (text: string): FileVersionDetail => ({ path: 'a.md', commit_id: 'a', type: 'markdown', content_text: text });
it('preserves both line numbers for a small modification', () => {
  const result = buildDiffPreview({ op: 'modified', previous: file('a\nb\nc'), current: file('a\nB\nc') });
  expect(result.lines).toContainEqual({ kind: 'remove', text: 'b', oldLine: 2 });
  expect(result.lines).toContainEqual({ kind: 'add', text: 'B', newLine: 2 });
  expect(result.lines).toContainEqual({ kind: 'context', text: 'c', oldLine: 3, newLine: 3 });
});
it.each(['added', 'deleted', 'modified'])('bounds %s DOM output on huge line counts', op => {
  const result = buildDiffPreview({ op, previous: file('old\n'.repeat(10_000)), current: file('new\n'.repeat(10_000)) });
  expect(result.lines!.length).toBeLessThanOrEqual(DIFF_MAX_ROWS + 1);
  expect(result.lines!.some(l => l.kind === 'hunk')).toBe(true);
});
it('declines large bodies before splitting and respects the server preview limit', () => {
  expect(buildDiffPreview({ op: 'added', current: file('x'.repeat(300_000)) }).lines).toBeNull();
  expect(buildDiffPreview({ op: 'added', current: { ...file(''), truncated: true } }).placeholder).toContain('Large file');
});
it('keeps binary data out of text previews', () => {
  expect(buildDiffPreview({ op: 'added', current: { ...file('abc'), is_binary: true } }).lines).toBeNull();
});
it('renders JSON responses and bounds long-line output', () => {
  const result = buildDiffPreview({ op: 'added', current: { ...file(''), content_text: null, content: { a: 1 } } });
  expect(result.lines!.some(line => line.text.includes('"a": 1'))).toBe(true);
  const long = buildDiffPreview({ op: 'added', current: file('x'.repeat(50_000)) });
  expect(long.lines!.map(l => l.text).join('').length).toBeLessThan(2100);
});
