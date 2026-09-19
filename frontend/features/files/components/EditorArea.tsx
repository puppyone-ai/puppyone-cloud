'use client';

import type { HtmlArtifactMode } from '@/components/editors/html/HtmlArtifactPreview';
import { type MarkdownViewMode } from '@/components/editors/markdown';
import type { CsvViewMode } from '@/components/editors/spreadsheet/CsvTableViewer';
import { PageLoading } from '@/components/loading';
import { hasConflictMarkers } from '@/lib/conflictMarkers';
import { isTextLikeCategory, resolveFormat } from '@/lib/fileFormats';
import { VIEWERS } from '@/lib/viewers/registry';
import dynamic from 'next/dynamic';

// Loaded on demand — only when user opens a GitHub-connected node.
// GitHub repos are a folder-shaped *connector*, not a file format,
// so they sit outside the file-format registry.
const GithubRepoView = dynamic(
  () => import('@/components/views/GithubRepoView').then((m) => ({ default: m.GithubRepoView })),
  { ssr: false }
);

import { ProjectWorkspaceView } from '@/components/ProjectWorkspaceView';
import type { EditorType } from '@/components/ProjectsHeader';
import type { McpToolPermissions } from '@/lib/mcpApi';

interface EditorAreaProps {
  activeNodeId: string;
  activeNodeType: string;
  activeMimeType: string | null;
  activeProject: { id: string; name: string } & Record<string, any>;
  currentTableData: any;
  /** Raw UTF-8 contents of the active file when its file format is
   *  text-like (markdown / code / yaml / csv / plaintext). For editable
   *  text formats this is the local draft and may differ from server.
   *  Empty for non-text formats. */
  textContent: string;
  /** True while the parent's text fetch is still pending. */
  isLoadingText: boolean;
  /** Markdown-only: WYSIWYG vs source toggle. */
  markdownViewMode: MarkdownViewMode;
  setMarkdownViewMode: (mode: MarkdownViewMode) => void;
  /** HTML artifact-only: sandboxed preview vs source. */
  htmlArtifactMode: HtmlArtifactMode;
  /** CSV/TSV-only: editable table, preview table, or source. */
  csvViewMode: CsvViewMode;
  /** Called by editable text viewers when the user types. */
  onTextChange: (content: string) => void;
  editorType: EditorType;
  configuredAccessPoints: { path: string; permissions: any }[];
  onActiveTableChange: (id: string) => void;
  onAccessPointChange: (path: string, permissions: McpToolPermissions) => void;
  onAccessPointRemove: (path: string) => void;
  onOpenDocument: (path: string, value: string) => void;
  onCreateTool: (path: string, value: any) => void;
}

export function EditorArea({
  activeNodeId,
  activeNodeType,
  activeMimeType,
  activeProject,
  currentTableData,
  textContent,
  isLoadingText,
  markdownViewMode,
  setMarkdownViewMode,
  htmlArtifactMode,
  csvViewMode,
  onTextChange,
  editorType,
  configuredAccessPoints,
  onActiveTableChange,
  onAccessPointChange,
  onAccessPointRemove,
  onOpenDocument,
  onCreateTool,
}: EditorAreaProps) {
  // Connector-shaped nodes are dispatched by `activeNodeType`, not
  // by file format — they don't have a single underlying file.
  if (activeNodeType === 'github') {
    return (
      <GithubRepoView
        nodeId={activeNodeId}
        nodeName={currentTableData?.name || ''}
        content={currentTableData?.content}
        syncUrl={currentTableData?.sync_url ?? undefined}
      />
    );
  }

  // Resolve the file format from the active node. `activeNodeId` is
  // a path so it carries the extension; `activeMimeType` is the
  // server-detected fallback. The registry guarantees a non-null
  // result (UNKNOWN_FORMAT for anything we don't know).
  const format = resolveFormat({ name: activeNodeId, mimeType: activeMimeType });

  // Special viewers consume non-standard props and are dispatched
  // here. Currently only `json-table` — it consumes `currentTableData`
  // (already parsed by the data hook) and the full workspace
  // plumbing, so it can't go through the generic VIEWERS registry.
  if (format.defaultViewer === 'json-table') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, minWidth: 0 }}>
        <div style={{ flex: 1, minHeight: 0, minWidth: 0, display: 'flex' }}>
          <ProjectWorkspaceView
            projectId={activeProject.id}
            project={activeProject}
            activeTableId={activeNodeId}
            onActiveTableChange={onActiveTableChange}
            onTreePathChange={() => {}}
            editorType={editorType}
            configuredAccessPoints={configuredAccessPoints}
            onAccessPointChange={onAccessPointChange}
            onAccessPointRemove={onAccessPointRemove}
            onOpenDocument={onOpenDocument}
            onCreateTool={onCreateTool}
          />
        </div>
      </div>
    );
  }

  // For text-like content (markdown / code / structured-as-text),
  // wait for the parent's text fetch to complete before mounting
  // the viewer — otherwise editors momentarily mount with an empty
  // string and flash a spinner of their own.
  const needsText = isTextLikeCategory(format);
  if (needsText && isLoadingText) {
    return <PageLoading variant="fill" />;
  }

  // The path is the canonical identity. `nodeName` is just the
  // final segment for display — UI chrome only, never logic.
  const nodeName = activeNodeId.split('/').pop() || '';

  // When the loaded text content carries Git-style conflict markers
  // (LWW landed both sides into the file), structured viewers like
  // MonacoJsonEditor and CsvTableViewer would fail to parse and
  // present a cryptic error or eat the markers silently. Force a
  // PlainTextEditor instead — it ships ConflictMarkerBanner, so the
  // user can see + resolve the conflict in-place. Only kicks in for
  // text-like formats; binary/HTML/PDF viewers don't carry text.
  let viewerId = format.defaultViewer;
  if (needsText && hasConflictMarkers(textContent)) {
    viewerId = 'plain-text';
  }
  const ViewerDef = VIEWERS[viewerId];
  const Viewer = ViewerDef.component;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, minWidth: 0 }}>
      <div style={{ flex: 1, minHeight: 0, minWidth: 0, display: 'flex' }}>
        <Viewer
          projectId={activeProject.id}
          filePath={activeNodeId}
          nodeName={nodeName}
          textContent={needsText ? textContent : undefined}
          isTextLoading={isLoadingText}
          monacoLanguage={format.monacoLanguage}
          formatLabel={format.label}
          mimeType={format.mimeTypes?.[0] ?? activeMimeType ?? undefined}
          editable={format.editable}
          onTextChange={format.editable ? onTextChange : undefined}
          markdownViewMode={markdownViewMode}
          onMarkdownViewModeChange={setMarkdownViewMode}
          htmlArtifactMode={htmlArtifactMode}
          csvViewMode={csvViewMode}
        />
      </div>
    </div>
  );
}
