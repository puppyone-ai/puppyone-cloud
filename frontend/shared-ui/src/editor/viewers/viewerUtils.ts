import { isTextLikeFile } from "../../core/fileFormats";
import type { EditorDocument } from "../viewerTypes";

export function isTextPreviewKind(type: EditorDocument["type"]): boolean {
  return type === "file" || type === "code" || type === "text";
}

export function isTextEditable(document: EditorDocument, content: string): boolean {
  if (document.type === "markdown" || document.type === "json") return true;
  if (!content) return false;
  if (!isTextPreviewKind(document.type)) return false;
  return isTextLikeFile(document.name, document.type, document.mimeType);
}

export function getDelimitedTableDelimiter(document: EditorDocument): "," | "\t" {
  if (
    document.name.toLowerCase().endsWith(".tsv") ||
    document.mimeType?.toLowerCase().startsWith("text/tab-separated-values")
  ) {
    return "\t";
  }
  return ",";
}

export function formatJson(content: string): string {
  if (!content.trim()) return content;
  try {
    return JSON.stringify(JSON.parse(content), null, 2);
  } catch {
    return content;
  }
}

export function getDocumentLabel(document: EditorDocument): string {
  return getFileExtension(document.name)?.toUpperCase() ?? document.type.toUpperCase();
}

export function getFileExtension(name: string): string | null {
  const index = name.lastIndexOf(".");
  if (index <= 0 || index === name.length - 1) return null;
  return name.slice(index + 1);
}
