import type { EditorDocument } from "../viewerTypes";

export function DocumentPreview({
  document,
  title,
}: {
  document: EditorDocument;
  title: string;
}) {
  const fallbackTitle = title.trim();
  const fileName = document.name || document.path || fallbackTitle || "File";
  const status = fallbackTitle && fallbackTitle !== fileName ? fallbackTitle : "Preview unavailable";
  const metadata = getDocumentMetadata(document, fallbackTitle, fileName);

  return (
    <div className="document-preview" role="status" aria-label={`${fileName}: preview unavailable`}>
      <section className="document-preview__summary">
        <span className="document-preview__eyebrow">File</span>
        <h2 className="document-preview__name">{fileName}</h2>
        <p className="document-preview__status">{status}</p>
        {metadata && <span className="document-preview__meta">{metadata}</span>}
      </section>
    </div>
  );
}

function getDocumentMetadata(document: EditorDocument, fallbackTitle: string, fileName: string): string {
  const values = [document.mimeType, formatDocumentType(document.type)]
    .filter((value): value is string => Boolean(value))
    .filter((value) => !sameLabel(value, fallbackTitle) && !sameLabel(value, fileName));

  return Array.from(new Set(values.map((value) => value.trim()))).join(" / ");
}

function formatDocumentType(type: EditorDocument["type"]): string | null {
  const normalized = type.trim();
  if (!normalized || normalized === "file") return null;
  if (normalized.endsWith(" file")) return normalized;
  return `${normalized} file`;
}

function sameLabel(left: string, right: string): boolean {
  return left.trim().toLowerCase() === right.trim().toLowerCase();
}
