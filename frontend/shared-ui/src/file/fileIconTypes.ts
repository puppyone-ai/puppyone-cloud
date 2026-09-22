export type FileVisualKind =
  | "folder"
  | "json"
  | "markdown"
  | "html"
  | "pdf"
  | "image"
  | "audio"
  | "video"
  | "spreadsheet"
  | "archive"
  | "document"
  | "binary"
  | "code"
  | "text"
  | "file";

export type FileIconThemeId = "default" | "lines" | "vscode" | "material" | "minimal";

export type FileIconThemeMetadata = {
  id: FileIconThemeId;
  label: string;
  description: string;
};

const KIND_ACCENT: Record<FileVisualKind, string> = {
  folder: "var(--po-file-accent-default)",
  json: "var(--po-file-accent-json)",
  markdown: "var(--po-file-accent-markdown)",
  html: "var(--po-file-accent-html)",
  pdf: "var(--po-file-accent-pdf)",
  image: "var(--po-file-accent-image)",
  audio: "var(--po-file-accent-audio)",
  video: "var(--po-file-accent-video)",
  spreadsheet: "var(--po-file-accent-sheet)",
  archive: "var(--po-file-accent-pdf)",
  document: "var(--po-file-accent-default)",
  binary: "var(--po-file-accent-default)",
  code: "var(--po-file-accent-code)",
  text: "var(--po-file-accent-default)",
  file: "var(--po-file-accent-default)",
};

export function getFileAccent(kind: FileVisualKind): string {
  return KIND_ACCENT[kind];
}
