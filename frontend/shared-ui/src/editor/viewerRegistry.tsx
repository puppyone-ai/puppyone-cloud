"use client";

import { resolveFileFormat } from "../core/fileFormats";
import type {
  EditorDocument,
  EditorSourceRequirement,
  EditorViewer,
  EditorViewerMatch,
} from "./viewerTypes";
import { JsonViewer, TextFileViewer, canEditTextFile } from "./viewers/CodeViewer";
import { CsvViewer, canEditCsv } from "./viewers/CsvViewer";
import { DocumentPreview } from "./viewers/DocumentFallbackViewer";
import { HtmlViewer } from "./viewers/HtmlViewer";
import { MarkdownViewer, canEditMarkdown } from "./viewers/MarkdownViewer";
import { OfficeViewer } from "./viewers/OfficeViewer";
import {
  AudioResourceViewer,
  ImageResourceViewer,
  PdfResourceViewer,
  VideoResourceViewer,
} from "./viewers/ResourceViewers";
import { formatJson, isTextPreviewKind } from "./viewers/viewerUtils";

export const EDITOR_VIEWERS: EditorViewer[] = [
  {
    id: "markdown",
    source: "content",
    match: ({ document, format }) => document.type === "markdown" || format.defaultViewer === "markdown-editor",
    isEditable: canEditMarkdown,
    render: (context) => <MarkdownViewer {...context} />,
  },
  {
    id: "json",
    source: "content",
    match: ({ document, format }) => document.type === "json" || format.id === "json" || format.id === "jsonl",
    normalizeContent: formatJson,
    isEditable: () => true,
    render: (context) => <JsonViewer {...context} />,
  },
  {
    id: "csv-table",
    source: "content",
    match: ({ format }) => format.defaultViewer === "csv-table",
    isEditable: canEditCsv,
    render: (context) => <CsvViewer {...context} />,
  },
  {
    id: "html-artifact",
    source: "content-and-resource",
    allowPreviewContent: false,
    match: ({ document, format }) => document.type === "html" || format.defaultViewer === "html-artifact",
    render: (context) => <HtmlViewer {...context} />,
  },
  {
    id: "image-preview",
    source: "resource",
    match: ({ document, format }) => document.type === "image" || format.defaultViewer === "image-preview",
    render: (context) => <ImageResourceViewer {...context} />,
  },
  {
    id: "pdf-preview",
    source: "resource",
    match: ({ document, format }) => document.type === "pdf" || format.defaultViewer === "pdf-preview",
    render: (context) => <PdfResourceViewer {...context} />,
  },
  {
    id: "office-preview",
    source: "resource",
    match: ({ document, format }) => format.defaultViewer === "office-preview" || isOfficeDocument(document.name, document.mimeType),
    render: (context) => <OfficeViewer {...context} />,
  },
  {
    id: "audio-preview",
    source: "resource",
    match: ({ document, format }) => document.type === "audio" || format.defaultViewer === "audio-preview",
    render: (context) => <AudioResourceViewer {...context} />,
  },
  {
    id: "video-preview",
    source: "resource",
    match: ({ document, format }) => document.type === "video" || format.defaultViewer === "video-preview",
    render: (context) => <VideoResourceViewer {...context} />,
  },
  {
    id: "text",
    source: "content",
    match: ({ document, format }) => (
      isTextPreviewKind(document.type) ||
      format.defaultViewer === "plain-text" ||
      format.defaultViewer === "monaco-code"
    ),
    isEditable: canEditTextFile,
    render: (context) => <TextFileViewer {...context} />,
  },
];

const FALLBACK_VIEWER: EditorViewer = {
  id: "document-placeholder",
  source: "none",
  match: () => true,
  render: ({ document, content }) => (
    <DocumentPreview document={document} title={content || "Binary file"} />
  ),
};

export function resolveEditorViewer(document: EditorDocument): { viewer: EditorViewer; format: EditorViewerMatch["format"] } {
  const format = resolveFileFormat({ name: document.name, mimeType: document.mimeType });
  const match = { document, format };
  return {
    viewer: EDITOR_VIEWERS.find((viewer) => viewer.match(match)) ?? FALLBACK_VIEWER,
    format,
  };
}

export function getEditorSourceRequirement(input: {
  name: string;
  type?: string | null;
  mimeType?: string | null;
}): EditorSourceRequirement {
  const { viewer } = resolveEditorViewer({
    path: input.name,
    name: input.name,
    type: input.type ?? "file",
    mimeType: input.mimeType ?? null,
  });
  return viewer.source;
}

export function shouldReadEditorContent(input: {
  name: string;
  type?: string | null;
  mimeType?: string | null;
}): boolean {
  const requirement = getEditorSourceRequirement(input);
  return requirement === "content" || requirement === "content-and-resource";
}

function isOfficeDocument(name: string, mimeType?: string | null): boolean {
  const lowerName = name.toLowerCase();
  if (/\.(docx?|xlsx?|xlsm|xlsb|pptx?|ppsx?|odt|ott|ods|ots|odp|otp)$/.test(lowerName)) return true;

  const normalizedMime = mimeType?.toLowerCase().split(";")[0].trim() ?? "";
  return (
    normalizedMime === "application/msword" ||
    normalizedMime === "application/vnd.ms-excel" ||
    normalizedMime === "application/vnd.ms-powerpoint" ||
    normalizedMime.startsWith("application/vnd.openxmlformats-officedocument.") ||
    normalizedMime.startsWith("application/vnd.oasis.opendocument.")
  );
}
