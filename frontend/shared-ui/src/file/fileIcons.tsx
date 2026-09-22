import type { ReactNode } from "react";
import { getFileSemanticKind, getMatchedExtension } from "../core/fileFormats";
import {
  FILE_ICON_THEMES,
  getFileIconThemeDefinition,
  isFileIconThemeId,
  type FileIconRenderContext,
} from "./fileIconThemeRegistry";
import { getFileAccent, type FileIconThemeId, type FileVisualKind } from "./fileIconTypes";

export { FILE_ICON_THEMES, getFileAccent, isFileIconThemeId };
export type { FileIconThemeId, FileVisualKind };

export function getFileExtension(name: string): string | null {
  return getMatchedExtension(name.trim());
}

export function getFileVisualKind(name: string, type?: string | null): FileVisualKind {
  return getFileSemanticKind(name, type);
}

export function FilePreviewIcon({
  name,
  type,
  size = 56,
  snippet,
  childrenCount,
  theme,
}: Readonly<{
  name: string;
  type?: string | null;
  size?: number;
  snippet?: string | null;
  childrenCount?: number | null;
  theme?: FileIconThemeId | null;
}>) {
  const context = createFileIconContext({ name, type, size });
  const themeDefinition = getFileIconThemeDefinition(theme);

  return themeDefinition.renderPreview({
    ...context,
    snippet,
    childrenCount,
  });
}

export function FileGlyphIcon({
  name,
  type,
  size = 18,
  theme,
}: Readonly<{
  name: string;
  type?: string | null;
  size?: number;
  theme?: FileIconThemeId | null;
}>) {
  const context = createFileIconContext({ name, type, size });
  return getFileIconThemeDefinition(theme).renderGlyph(context);
}

export function getFileIcon(filename: string, size = 48, theme?: FileIconThemeId | null): ReactNode {
  return <FilePreviewIcon name={filename} size={size} theme={theme} />;
}

export const FILE_TYPE_ICONS = {
  folder: <FileGlyphIcon name="folder" type="folder" size={14} />,
  table: <FileGlyphIcon name="data.json" type="json" size={14} />,
  markdown: <FileGlyphIcon name="document.md" type="markdown" size={14} />,
};

function createFileIconContext({
  name,
  type,
  size,
}: {
  name: string;
  type?: string | null;
  size: number;
}): FileIconRenderContext {
  const kind = getFileVisualKind(name, type);

  return {
    kind,
    name,
    type,
    label: getLabel(kind, name),
    size,
    color: getFileAccent(kind),
  };
}

function getLabel(kind: FileVisualKind, name: string): string {
  const semanticLabel = FILE_KIND_LABELS[kind];
  if (semanticLabel) return semanticLabel;
  return getFileExtension(name)?.toUpperCase().slice(0, 4) || "FILE";
}

const FILE_KIND_LABELS: Partial<Record<FileVisualKind, string>> = {
  audio: "MP3",
  html: "HTML",
  image: "IMG",
  json: "{}",
  markdown: "DOC",
  pdf: "PDF",
};
