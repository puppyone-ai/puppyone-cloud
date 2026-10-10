import { isTextLikeCategory, resolveFormat, type GenericViewerId } from '@/lib/fileFormats';
import { hasConflictMarkers } from '@/lib/conflictMarkers';

/** A bounded preview owns its bytes. Viewers must never fetch a larger file. */
export type PreviewSource = { kind: 'text' } | { kind: 'image'; url: string } | { kind: 'unavailable' };

export function resolveViewer({ name, mimeType, textContent, preview, literalText = false }: {
  name: string; mimeType?: string | null; textContent?: string;
  preview?: PreviewSource; literalText?: boolean;
}) {
  const format = resolveFormat({ name, mimeType });
  // The metadata-based table is composed by EditorArea, not a generic file viewer.
  let viewerId: GenericViewerId = format.defaultViewer === 'json-table' ? 'monaco-code' : format.defaultViewer;
  if (preview?.kind === 'unavailable') return { format, viewerId: 'binary-placeholder' as const };
  if (preview?.kind === 'image') return { format, viewerId: 'image-preview' as const };
  if (literalText || (preview?.kind === 'text' && !isTextLikeCategory(format))) viewerId = 'plain-text';
  if (textContent !== undefined && (preview?.kind === 'text' || isTextLikeCategory(format)) && hasConflictMarkers(textContent)) {
    viewerId = 'plain-text';
  }
  return { format, viewerId };
}
