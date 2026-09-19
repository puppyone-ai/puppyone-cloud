'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { EditorView } from '@codemirror/view';
import { preserveMarkdownScroll } from '@/features/files/editor/preserveMarkdownScroll';
import { MarkdownCodeMirrorEditor } from '@/shared-ui/src/editor/markdown/MarkdownCodeMirrorEditor';

export type MarkdownViewMode = 'wysiwyg' | 'source';

interface MarkdownEditorProps {
  content: string;
  onChange?: (content: string) => void;
  readOnly?: boolean;
  documentKey?: string;
  defaultMode?: MarkdownViewMode;
  viewMode?: MarkdownViewMode;
  onViewModeChange?: (mode: MarkdownViewMode) => void;
}

export function MarkdownEditor({
  content,
  onChange,
  readOnly = false,
  documentKey,
  defaultMode = 'wysiwyg',
  viewMode: controlledViewMode,
  onViewModeChange,
}: MarkdownEditorProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [internalViewMode, setInternalViewMode] = useState<MarkdownViewMode>(defaultMode);
  const [localContent, setLocalContent] = useState(content);
  const isControlled = controlledViewMode !== undefined;
  const rawViewMode = isControlled ? controlledViewMode : internalViewMode;
  const viewMode: MarkdownViewMode = rawViewMode === 'source' ? 'source' : 'wysiwyg';
  const canEdit = !readOnly && Boolean(onChange);

  useEffect(() => {
    setLocalContent(content);
  }, [content]);

  const setViewMode = isControlled ? (mode: MarkdownViewMode) => onViewModeChange?.(mode) : setInternalViewMode;

  const handleChange = useCallback((newContent: string) => {
    setLocalContent(newContent);
    if (onChange && !readOnly) onChange(newContent);
  }, [onChange, readOnly]);

  useLayoutEffect(() => {
    // CodeMirror exposes its view via the public DOM lookup API. Keep this
    // cloud-only layout adapter outside the shared Cloud/Desktop editor.
    const editor = hostRef.current?.querySelector<HTMLElement>('.cm-editor');
    const view = editor ? EditorView.findFromDOM(editor) : null;
    if (view) return preserveMarkdownScroll(view);
  }, [documentKey, viewMode]);

  return (
    <div
      ref={hostRef}
      style={{
        height: '100%',
        width: '100%',
        position: 'relative',
        background: 'var(--po-editor-bg)',
      }}
    >
      <MarkdownCodeMirrorEditor
        key={`${documentKey ?? 'markdown'}:${viewMode}`}
        value={localContent}
        onChange={canEdit ? handleChange : undefined}
        readOnly={!canEdit}
        livePreview={viewMode === 'wysiwyg'}
      />

      {!isControlled && (
        <div className="editor-mode-toggle" aria-label="Markdown editor mode">
          <button
            className={viewMode === 'wysiwyg' ? 'active' : ''}
            type="button"
            onClick={() => setViewMode('wysiwyg')}
            title="Live view"
            aria-label="Live view"
          >
            <PencilIcon />
          </button>
          <button
            className={viewMode === 'source' ? 'active' : ''}
            type="button"
            onClick={() => setViewMode('source')}
            title="Source"
            aria-label="Source"
          >
            <CodeIcon />
          </button>
        </div>
      )}
    </div>
  );
}

function PencilIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
    </svg>
  );
}

function CodeIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
    </svg>
  );
}

export default MarkdownEditor;
