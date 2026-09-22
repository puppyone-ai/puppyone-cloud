import type { ReactNode } from "react";
import type { FileFormat } from "../core/fileFormats";
import type { FileIconThemeId } from "../file/fileIcons";
import type { AiEditFile } from "./ai-edits/types";

export type EditorDocumentKind =
  | "folder"
  | "markdown"
  | "json"
  | "html"
  | "image"
  | "audio"
  | "pdf"
  | "video"
  | "spreadsheet"
  | "archive"
  | "document"
  | "binary"
  | "code"
  | "text"
  | "file"
  | string;

export type EditorDocument = {
  path: string;
  name: string;
  type: EditorDocumentKind;
  content?: string | null;
  preview?: string | null;
  mimeType?: string | null;
  url?: string | null;
};

export type EditorMode = "live" | "source";
export type EditorSaveMode = "manual" | "auto";
export type EditorSourceRequirement = "content" | "resource" | "content-and-resource" | "none";
export type MarkdownHtmlTrustMode = "safe" | "localTrusted";

export type MarkdownWikiLinkResolvedTarget = {
  exists: boolean;
  ambiguous: boolean;
  path: string | null;
  candidatePaths?: string[];
  name: string;
  displayName: string;
  target: string;
  heading?: string | null;
};

export type MarkdownBacklinkReference = {
  lineNumber: number;
  lineText: string;
  target: string;
  label: string;
};

export type MarkdownBacklink = {
  sourcePath: string;
  sourceName: string;
  count: number;
  references: MarkdownBacklinkReference[];
};

export type MarkdownLinkGraph = {
  documentCount: number;
  indexedDocumentCount: number;
  isIndexing: boolean;
  resolveWikiLink: (sourcePath: string, target: string) => MarkdownWikiLinkResolvedTarget;
  resolveMarkdownLink: (sourcePath: string, href: string) => MarkdownWikiLinkResolvedTarget | null;
  openWikiLink?: (target: MarkdownWikiLinkResolvedTarget, sourcePath: string) => void;
  openPath?: (path: string) => void;
  openExternalUrl?: (href: string) => void;
  getBacklinks?: (path: string) => MarkdownBacklink[];
};

export type EditorViewerMatch = {
  document: EditorDocument;
  format: FileFormat;
};

export type EditorViewerContext = EditorViewerMatch & {
  content: string;
  aiEditFile?: AiEditFile | null;
  fileUrl?: string | null;
  fileUrlLoading: boolean;
  fileUrlError?: string | null;
  loading: boolean;
  error?: string | null;
  canEdit: boolean;
  hideSourceView: boolean;
  fileIconTheme: FileIconThemeId;
  saveMode: EditorSaveMode;
  htmlTrustMode: MarkdownHtmlTrustMode;
  markdownLinkGraph?: MarkdownLinkGraph | null;
  onSaveContent?: (content: string) => Promise<void>;
};

export type EditorViewer = {
  id: string;
  source: EditorSourceRequirement;
  match: (match: EditorViewerMatch) => boolean;
  allowPreviewContent?: boolean;
  normalizeContent?: (content: string, document: EditorDocument) => string;
  isEditable?: (match: EditorViewerMatch & { content: string }) => boolean;
  render: (context: EditorViewerContext) => ReactNode;
};
