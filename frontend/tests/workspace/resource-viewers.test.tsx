import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ResourceContent, resourceViewerId } from '@/features/resource-preview/ResourceContent';
import { FileViewer } from '@/lib/viewers/FileViewer';
import { resolveViewer } from '@/lib/viewers/resolveViewer';
import { FileViewerModePicker } from '@/features/files/components/FileViewerHeaderActions';
import type { WorkspaceResource } from '@/features/resource-preview/resourceApi';

// Keep real adapters/editors; only replace Monaco's browser/worker boundary in jsdom.
vi.mock('@monaco-editor/react', () => ({ default: (props: any) => <textarea aria-label="Code source"
  data-language={props.language ?? props.defaultLanguage} readOnly={props.options.readOnly} value={props.value}
  onChange={event => props.onChange?.(event.target.value)} /> }));
vi.mock('@/contexts/SupabaseAuthProvider', () => ({ useAuth: () => ({ userId: null }) }));
vi.mock('next/navigation', () => ({ useParams: () => ({}) }));

function file(name: string, text: string, extra: Partial<WorkspaceResource> = {}): WorkspaceResource {
  return { kind: 'file', path: name, entry: { name, path: name, type: 'file', mime_type: 'text/plain',
    git_mode: '100644', size_bytes: text.length, path_bytes_b64: null }, entries: [], next_cursor: null,
    repository_revision: { tree_oid: 'tree', expected_oid: 'commit', generation: 1 },
    preview: 'text', content_text: text, content_base64: null, truncated: false, ...extra };
}
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class {
    constructor(private callback: ResizeObserverCallback) {}
    observe(target: Element) { this.callback([{ target, contentRect: new DOMRect(0, 0, 1000, 600), borderBoxSize: [{ inlineSize: 1000, blockSize: 600 }] } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver); }
    unobserve() {} disconnect() {}
  });
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 0, 1000, 600));
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(1000);
});

it.each([['notes.md', 'markdown-editor'], ['data.csv', 'csv-table'], ['data.tsv', 'csv-table'],
  ['data.json', 'monaco-code'], ['script.py', 'monaco-code'], ['page.html', 'html-artifact'], ['note.txt', 'plain-text']])(
  'shares the Workspace format resolution for %s', (name, id) => {
    expect(resourceViewerId(file(name, ''))).toBe(id);
    expect(resolveViewer({ name }).viewerId).toBe(id);
  });

it.each(['csv', 'tsv'])('renders %s with the native table and preserves a read-only source view', async extension => {
  const separator = extension === 'tsv' ? '\t' : ',';
  const resource = file(`data.${extension}`, `Name${separator}Count\nAlice${separator}42\nBob${separator}7`);
  const view = render(<ResourceContent resource={resource} csvViewMode="preview" />);
  await screen.findByRole('grid');
  expect(await screen.findByText('Alice')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Add row' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Add column' })).toBeNull();
  fireEvent.doubleClick(screen.getByText('Alice'));
  expect(view.container.querySelector('[contenteditable="true"]')).toBeNull();
  view.rerender(<ResourceContent resource={resource} csvViewMode="source" />);
  const source = await screen.findByRole('textbox', { name: 'Code source' }) as HTMLTextAreaElement;
  expect(source.readOnly).toBe(true);
  expect(source.value).toBe(resource.content_text);
});

it('renders JSON with the same Monaco source view configured in Workspace', async () => {
  const resource = file('data.json', '{"name":"Alice","stats":{"count":42},"empty":null}');
  render(<ResourceContent resource={resource} />);
  const source = await screen.findByRole('textbox', { name: 'Code source' }) as HTMLTextAreaElement;
  expect(source.readOnly).toBe(true);
  expect(source.dataset.language).toBe('json');
  expect(source.value).toBe(resource.content_text);
});

it.each(['{"unfinished":', 'null', '42', '"hello"'])('keeps incomplete or primitive JSON visible: %s', async text => {
  render(<ResourceContent resource={file('data.json', text, { truncated: true })} />);
  const source = await screen.findByRole('textbox', { name: 'Code source' }) as HTMLTextAreaElement;
  expect(source.value).toBe(text);
  expect(source.readOnly).toBe(true);
  expect(screen.getByText(/This preview is limited/)).toBeTruthy();
});

it('does not render residual text when the server marks the preview unavailable', () => {
  expect(resourceViewerId(file('notes.md', '<<<<<<< HEAD\nprivate\n=======\nsecret\n>>>>>>> main', { preview: 'unavailable' }))).toBe('binary-placeholder');
});

it('uses native code highlighting and never passes a preview mutation callback', async () => {
  const onChange = vi.fn();
  render(<FileViewer projectId="p" filePath="script.py" nodeName="script.py" textContent="print(42)"
    preview={{ kind: 'text' }} editable onTextChange={onChange} />);
  const source = await screen.findByRole('textbox', { name: 'Code source' }) as HTMLTextAreaElement;
  expect(source.dataset.language).toBe('python');
  expect(source.readOnly).toBe(true);
  fireEvent.change(source, { target: { value: 'changed' } });
  expect(onChange).not.toHaveBeenCalled();
});

it('keeps Workspace editing enabled through the common viewer dispatch', async () => {
  const onChange = vi.fn();
  render(<FileViewer projectId="p" filePath="script.py" nodeName="script.py" textContent="print(42)" editable onTextChange={onChange} />);
  const source = await screen.findByRole('textbox', { name: 'Code source' }) as HTMLTextAreaElement;
  expect(source.readOnly).toBe(false);
  fireEvent.change(source, { target: { value: 'print(7)' } });
  expect(onChange).toHaveBeenCalledWith('print(7)');
});

it('shows Git symlinks and conflict markers literally, not as structured documents', async () => {
  const resource = file('data.json', '../private/data.json');
  resource.entry.git_mode = '120000';
  expect(resourceViewerId(resource)).toBe('plain-text');
  expect(resourceViewerId(file('data.csv', '<<<<<<< HEAD\nA\n=======\nB\n>>>>>>> branch'))).toBe('plain-text');
  render(<ResourceContent resource={resource} />);
  await screen.findByText('../private/data.json');
});

it('uses the native HTML preview with scripts and external resource loading disabled', async () => {
  const view = render(<ResourceContent resource={file('page.html', '<h1>Hello</h1><script>alert(1)</script>')} />);
  const iframe = await screen.findByTitle('page.html') as HTMLIFrameElement;
  expect(iframe.sandbox?.value ?? iframe.getAttribute('sandbox')).not.toContain('allow-scripts');
  expect(iframe.srcdoc).toContain("script-src 'none'");
  expect(iframe.srcdoc).toContain('img-src data: blob:;');
  view.rerender(<ResourceContent resource={file('page.html', '<h1>Hello</h1>')} htmlArtifactMode="source" />);
  expect((await screen.findByRole('textbox', { name: 'Code source' }) as HTMLTextAreaElement).readOnly).toBe(true);
});

it('does not fetch raw files for unavailable previews or inline images', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  const view = render(<ResourceContent resource={file('video.mp4', '', { preview: 'unavailable', content_text: null })} />);
  await screen.findByText('video.mp4');
  const image = file('picture.png', '', { preview: 'image', content_base64: 'AAAA', content_text: null });
  image.entry.mime_type = 'image/png';
  view.rerender(<ResourceContent resource={image} />);
  await waitFor(() => expect(view.container.querySelector('img')?.src).toBe('data:image/png;base64,AAAA'));
  expect(fetch).not.toHaveBeenCalled();
});

it('reuses the mode menu without advertising editing in a share', () => {
  render(<FileViewerModePicker viewerId="csv-table" editable={false} csvViewMode="preview" onCsvViewModeChange={vi.fn()}
    markdownViewMode="wysiwyg" onMarkdownViewModeChange={vi.fn()} editorType="table" onEditorTypeChange={vi.fn()}
    htmlMode="preview" onHtmlModeChange={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'View mode: Preview table' }));
  expect(screen.getByRole('menuitem', { name: 'Source' })).toBeTruthy();
  expect(screen.queryByRole('menuitem', { name: 'Edit table' })).toBeNull();
});
