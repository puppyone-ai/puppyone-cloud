/**
 * Generic Viewer Registry — maps `GenericViewerId` to the React
 * component that renders that file.
 *
 * Adding a new generic viewer:
 *   1. Add the id to `GenericViewerId` in `lib/fileFormats/types.ts`.
 *   2. Add an entry to `VIEWERS` below.
 *   3. Reference the id from one or more `FileFormat.defaultViewer` /
 *      `FileFormat.availableViewers` values in `registry.ts`.
 *
 * "Special" viewers (currently just `json-table`) consume
 * non-standard props and are dispatched directly inside
 * `<EditorArea>` — they are intentionally absent here.
 */

import dynamic from 'next/dynamic';
import { ComponentType } from 'react';
import { EditorLoadingSurface } from '@/components/loading';
import { PlainTextEditor } from '@/components/editors/text';
import type { GenericViewerId } from '@/lib/fileFormats/types';
import type { MarkdownViewMode } from '@/components/editors/markdown';
import type { HtmlArtifactMode } from '@/components/editors/html/HtmlArtifactPreview';
import type { CsvViewMode } from '@/components/editors/spreadsheet/CsvTableViewer';

/**
 * Common props every viewer accepts. Individual viewers can ignore
 * fields they don't need — that's a feature, not a bug. The dispatch
 * layer (`<EditorArea>`) passes the same blob of context to whichever
 * viewer the registry resolves to.
 */
export interface ViewerProps {
  projectId: string;
  filePath: string;
  nodeName: string;
  /** UTF-8 text for text-like categories. Empty string when not loaded. */
  textContent?: string;
  /** Whether the underlying text fetch is still pending. */
  isTextLoading?: boolean;
  /** Pre-resolved Monaco language id (from FileFormat.monacoLanguage). */
  monacoLanguage?: string;
  /** Format label, e.g. "PDF Document". Used by placeholders. */
  formatLabel?: string;
  /** Resolved MIME type (e.g. 'video/mp4'). Audio/video viewers
   *  forward this to `<source type>` for deterministic codec routing. */
  mimeType?: string;
  /** Whether the user is allowed to mutate this file. */
  editable?: boolean;
  /** Called by editable viewers when content changes. */
  onTextChange?: (value: string) => void;
  /** Markdown-only: WYSIWYG vs source-mode toggle. Other viewers ignore. */
  markdownViewMode?: MarkdownViewMode;
  onMarkdownViewModeChange?: (mode: MarkdownViewMode) => void;
  /** HTML artifact-only: sandboxed preview vs source. Other viewers ignore. */
  htmlArtifactMode?: HtmlArtifactMode;
  /** CSV/TSV-only: table edit, table preview, or raw source. Other viewers ignore. */
  csvViewMode?: CsvViewMode;
}

export interface ViewerDefinition {
  id: GenericViewerId;
  /** The component to render. May be a dynamic import for heavy viewers. */
  component: ComponentType<ViewerProps>;
  /** Whether this viewer needs `textContent` populated before render. */
  requiresText: boolean;
}

const PageLoadingFallback = () => <EditorLoadingSurface />;

// Each viewer is a thin adapter from `ViewerProps` to the
// component's native API. This keeps the components themselves
// agnostic of the registry — they're still usable standalone.

const MarkdownEditorAdapter = dynamic<ViewerProps>(
  () =>
    import('@/components/editors/markdown').then((mod) => {
      const { MarkdownEditor } = mod;
      const Adapter = ({
        filePath,
        textContent,
        editable,
        onTextChange,
        markdownViewMode,
        onMarkdownViewModeChange,
      }: ViewerProps) => (
        <MarkdownEditor
          content={textContent ?? ''}
          onChange={editable ? onTextChange : undefined}
          readOnly={!editable}
          documentKey={filePath}
          viewMode={markdownViewMode}
          onViewModeChange={onMarkdownViewModeChange}
        />
      );
      Adapter.displayName = 'MarkdownEditorAdapter';
      return Adapter;
    }),
  { ssr: false, loading: PageLoadingFallback },
);

function PlainTextAdapter({
  textContent,
  nodeName,
  editable,
  onTextChange,
}: ViewerProps) {
  return (
    <PlainTextEditor
      content={textContent ?? ''}
      nodeName={nodeName}
      readOnly={!editable}
      onChange={editable ? onTextChange : undefined}
    />
  );
}

const MonacoCodeAdapter = dynamic<ViewerProps>(
  () =>
    import('@/components/editors/code/MonacoCodeViewer').then((mod) => {
      const { MonacoCodeViewer } = mod;
      const Adapter = ({
        textContent,
        monacoLanguage,
        editable,
        onTextChange,
      }: ViewerProps) => (
        <MonacoCodeViewer
          content={textContent ?? ''}
          language={monacoLanguage ?? 'plaintext'}
          readOnly={!editable}
          onChange={editable ? onTextChange : undefined}
        />
      );
      Adapter.displayName = 'MonacoCodeAdapter';
      return Adapter;
    }),
  { ssr: false, loading: PageLoadingFallback },
);

const CsvTableAdapter = dynamic<ViewerProps>(
  () =>
    import('@/components/editors/spreadsheet/CsvTableViewer').then((mod) => {
      const { CsvTableViewer } = mod;
      const Adapter = ({
        textContent,
        filePath,
        nodeName,
        editable,
        onTextChange,
        csvViewMode,
      }: ViewerProps) => (
        <CsvTableViewer
          content={textContent ?? ''}
          filePath={filePath}
          nodeName={nodeName}
          mode={csvViewMode ?? 'preview'}
          readOnly={!editable}
          onChange={editable ? onTextChange : undefined}
        />
      );
      Adapter.displayName = 'CsvTableAdapter';
      return Adapter;
    }),
  { ssr: false, loading: PageLoadingFallback },
);

const HtmlArtifactAdapter = dynamic<ViewerProps>(
  () =>
    import('@/components/editors/html/HtmlArtifactPreview').then((mod) => {
      const { HtmlArtifactPreview } = mod;
      const Adapter = ({ textContent, nodeName, htmlArtifactMode }: ViewerProps) => (
        <HtmlArtifactPreview
          content={textContent ?? ''}
          nodeName={nodeName}
          mode={htmlArtifactMode ?? 'preview'}
        />
      );
      Adapter.displayName = 'HtmlArtifactAdapter';
      return Adapter;
    }),
  { ssr: false, loading: PageLoadingFallback },
);

const ImagePreviewAdapter = dynamic<ViewerProps>(
  () =>
    import('@/components/editors/image/ImagePreview').then((mod) => {
      const { ImagePreview } = mod;
      const Adapter = ({ projectId, filePath, nodeName }: ViewerProps) => (
        <ImagePreview projectId={projectId} filePath={filePath} nodeName={nodeName} />
      );
      Adapter.displayName = 'ImagePreviewAdapter';
      return Adapter;
    }),
  { ssr: false, loading: PageLoadingFallback },
);

const AudioPreviewAdapter = dynamic<ViewerProps>(
  () =>
    import('@/components/editors/audio/AudioPreview').then((mod) => {
      const { AudioPreview } = mod;
      const Adapter = ({ projectId, filePath, nodeName, mimeType }: ViewerProps) => (
        <AudioPreview
          projectId={projectId}
          filePath={filePath}
          nodeName={nodeName}
          mimeType={mimeType}
        />
      );
      Adapter.displayName = 'AudioPreviewAdapter';
      return Adapter;
    }),
  { ssr: false, loading: PageLoadingFallback },
);

const VideoPreviewAdapter = dynamic<ViewerProps>(
  () =>
    import('@/components/editors/video/VideoPreview').then((mod) => {
      const { VideoPreview } = mod;
      const Adapter = ({ projectId, filePath, nodeName, mimeType }: ViewerProps) => (
        <VideoPreview
          projectId={projectId}
          filePath={filePath}
          nodeName={nodeName}
          mimeType={mimeType}
        />
      );
      Adapter.displayName = 'VideoPreviewAdapter';
      return Adapter;
    }),
  { ssr: false, loading: PageLoadingFallback },
);

const PdfPreviewAdapter = dynamic<ViewerProps>(
  () =>
    import('@/components/editors/pdf/PdfPreview').then((mod) => {
      const { PdfPreview } = mod;
      const Adapter = ({ projectId, filePath, nodeName }: ViewerProps) => (
        <PdfPreview projectId={projectId} filePath={filePath} nodeName={nodeName} />
      );
      Adapter.displayName = 'PdfPreviewAdapter';
      return Adapter;
    }),
  { ssr: false, loading: PageLoadingFallback },
);

const BinaryPlaceholderAdapter = dynamic<ViewerProps>(
  () =>
    import('@/components/editors/binary/BinaryPlaceholder').then((mod) => {
      const { BinaryPlaceholder } = mod;
      const Adapter = ({ nodeName, formatLabel }: ViewerProps) => (
        <BinaryPlaceholder nodeName={nodeName} formatLabel={formatLabel} />
      );
      Adapter.displayName = 'BinaryPlaceholderAdapter';
      return Adapter;
    }),
  { ssr: false, loading: PageLoadingFallback },
);

export const VIEWERS: Record<GenericViewerId, ViewerDefinition> = {
  'markdown-editor': {
    id: 'markdown-editor',
    component: MarkdownEditorAdapter,
    requiresText: true,
  },
  'plain-text': {
    id: 'plain-text',
    component: PlainTextAdapter,
    requiresText: true,
  },
  'monaco-code': {
    id: 'monaco-code',
    component: MonacoCodeAdapter,
    requiresText: true,
  },
  'csv-table': {
    id: 'csv-table',
    component: CsvTableAdapter,
    requiresText: true,
  },
  'html-artifact': {
    id: 'html-artifact',
    component: HtmlArtifactAdapter,
    requiresText: true,
  },
  'image-preview': {
    id: 'image-preview',
    component: ImagePreviewAdapter,
    requiresText: false,
  },
  'audio-preview': {
    id: 'audio-preview',
    component: AudioPreviewAdapter,
    requiresText: false,
  },
  'video-preview': {
    id: 'video-preview',
    component: VideoPreviewAdapter,
    requiresText: false,
  },
  'pdf-preview': {
    id: 'pdf-preview',
    component: PdfPreviewAdapter,
    requiresText: false,
  },
  'office-preview': {
    id: 'office-preview',
    component: BinaryPlaceholderAdapter,
    requiresText: false,
  },
  'binary-placeholder': {
    id: 'binary-placeholder',
    component: BinaryPlaceholderAdapter,
    requiresText: false,
  },
};

export function getViewer(id: GenericViewerId): ViewerDefinition {
  return VIEWERS[id];
}
