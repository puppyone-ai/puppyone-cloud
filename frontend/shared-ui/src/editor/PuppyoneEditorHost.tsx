"use client";

import { resolveEditorViewer } from "./viewerRegistry";
import type { EditorDocument, EditorSaveMode, MarkdownHtmlTrustMode, MarkdownLinkGraph } from "./viewerTypes";
import type { FileIconThemeId } from "../file/fileIcons";
import type { AiEditFile } from "./ai-edits/types";

export type { EditorDocument, EditorDocumentKind, EditorSaveMode, MarkdownHtmlTrustMode } from "./viewerTypes";

export type PuppyoneEditorHostProps = {
  document: EditorDocument;
  loading?: boolean;
  error?: string | null;
  fileUrlLoading?: boolean;
  fileUrlError?: string | null;
  onSaveContent?: (content: string) => Promise<void>;
  aiEditFile?: AiEditFile | null;
  hideSourceView?: boolean;
  fileIconTheme?: FileIconThemeId;
  saveMode?: EditorSaveMode;
  htmlTrustMode?: MarkdownHtmlTrustMode;
  markdownLinkGraph?: MarkdownLinkGraph | null;
};

export function PuppyoneEditorHost({
  document,
  loading = false,
  error = null,
  fileUrlLoading = false,
  fileUrlError = null,
  onSaveContent,
  aiEditFile = null,
  hideSourceView = false,
  fileIconTheme = "default",
  saveMode = "manual",
  htmlTrustMode = "safe",
  markdownLinkGraph = null,
}: PuppyoneEditorHostProps) {
  const { viewer, format } = resolveEditorViewer(document);
  const rawContent = viewer.allowPreviewContent === false
    ? document.content ?? ""
    : document.content ?? document.preview ?? "";
  const content = viewer.normalizeContent?.(rawContent, document) ?? rawContent;
  const canEdit = Boolean(onSaveContent && viewer.isEditable?.({ document, format, content }));

  if (viewer.source !== "resource" && loading && !content) {
    return <div className="editor-state">Loading file...</div>;
  }

  if (viewer.source !== "resource" && error && !content) {
    return <div className="editor-state danger">{error}</div>;
  }

  return (
    <>
      {viewer.render({
        document,
        format,
        content,
        aiEditFile,
        fileUrl: document.url,
        fileUrlLoading,
        fileUrlError,
        loading,
        error,
        canEdit,
        hideSourceView,
        fileIconTheme,
        saveMode,
        htmlTrustMode,
        markdownLinkGraph,
        onSaveContent,
      })}
    </>
  );
}
