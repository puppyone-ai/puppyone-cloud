/**
 * File Format resolution — extension first, mime fallback.
 *
 * Hot path. We pre-build two flat lookup maps at module load so
 * `resolveFormat` is O(1) and safe to call inside renders.
 */

import { FILE_FORMATS, UNKNOWN_FORMAT } from './registry';
import type { FileFormat } from './types';

// Three-level lookup: filename → extension → mime. Filename wins
// for no-extension files like `Makefile`, `Dockerfile`, `README`.
const FILENAME_INDEX = new Map<string, FileFormat>();
const EXT_INDEX = new Map<string, FileFormat>();
const MIME_INDEX = new Map<string, FileFormat>();
const FILENAME_PATTERNS: Array<{ regex: RegExp; format: FileFormat }> = [];

for (const fmt of FILE_FORMATS) {
  for (const filename of fmt.filenames ?? []) {
    FILENAME_INDEX.set(filename.toLowerCase(), fmt);
  }
  for (const pattern of fmt.filenamePatterns ?? []) {
    FILENAME_PATTERNS.push({
      regex: globPatternToRegExp(pattern.toLowerCase()),
      format: fmt,
    });
  }
  for (const ext of fmt.extensions ?? []) {
    EXT_INDEX.set(ext.toLowerCase(), fmt);
  }
  for (const mime of fmt.mimeTypes ?? []) {
    MIME_INDEX.set(mime.toLowerCase(), fmt);
  }
}

/**
 * Strip directories so we match only the basename. Both `/` and `\`
 * are treated as separators because the path can come from either
 * server (always `/`) or a Windows drag-drop (`\`).
 */
function basename(path: string): string {
  const lastSlash = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  return lastSlash >= 0 ? path.slice(lastSlash + 1) : path;
}

/**
 * Extract the longest matching extension suffix from a filename.
 * '.tar.gz' beats '.gz' for `archive.tar.gz`.
 */
function matchExtension(name: string): FileFormat | null {
  const lower = name.toLowerCase();
  // Try compound (two-segment) extension first — '.tar.gz' etc.
  const lastDot = lower.lastIndexOf('.');
  if (lastDot < 0) return null;
  const secondLastDot = lower.lastIndexOf('.', lastDot - 1);
  if (secondLastDot >= 0) {
    const compound = lower.slice(secondLastDot);
    const compoundMatch = EXT_INDEX.get(compound);
    if (compoundMatch) return compoundMatch;
  }
  return EXT_INDEX.get(lower.slice(lastDot)) ?? null;
}

function matchFilenamePattern(name: string): FileFormat | null {
  const normalized = name.replace(/\\/g, '/').toLowerCase();
  const base = basename(normalized);

  for (const { regex, format } of FILENAME_PATTERNS) {
    if (regex.test(normalized) || regex.test(base)) return format;
  }

  return null;
}

function globPatternToRegExp(pattern: string): RegExp {
  let source = '^';

  for (let index = 0; index < pattern.length; index += 1) {
    const char = pattern[index];
    const next = pattern[index + 1];
    const afterNext = pattern[index + 2];

    if (char === '*' && next === '*' && afterNext === '/') {
      source += '(?:.*/)?';
      index += 2;
      continue;
    }

    if (char === '*' && next === '*') {
      source += '.*';
      index += 1;
      continue;
    }

    if (char === '*') {
      source += '[^/]*';
      continue;
    }

    if (char === '?') {
      source += '[^/]';
      continue;
    }

    source += escapeRegExp(char);
  }

  return new RegExp(`${source}$`);
}

function escapeRegExp(value: string): string {
  return value.replace(/[\\^$.*+?()[\]{}|]/g, '\\$&');
}

export interface ResolveInput {
  /** File path or just the filename — anything containing the extension. */
  name?: string | null;
  /** Server-detected mime type, used as fallback. */
  mimeType?: string | null;
}

/**
 * Returns the best-matching FileFormat for a (filename, mime) pair.
 * Always returns a value — `UNKNOWN_FORMAT` if nothing matches.
 *
 * Resolution order: exact basename → extension → mime fallback.
 */
export function resolveFormat(input: ResolveInput): FileFormat {
  const { name, mimeType } = input;
  if (name) {
    const base = basename(name).toLowerCase();
    const byName = FILENAME_INDEX.get(base);
    if (byName) return byName;
    const byExt = matchExtension(name);
    if (byExt) return byExt;
    const byPattern = matchFilenamePattern(name);
    if (byPattern) return byPattern;
  }
  if (mimeType) {
    const byMime = MIME_INDEX.get(mimeType.toLowerCase());
    if (byMime) return byMime;
    // Loose fallback: a server-detected `image/foo` we don't know
    // about (e.g. `image/heic-sequence`) should still hit ImagePreview.
    if (mimeType.startsWith('image/')) {
      return {
        ...UNKNOWN_FORMAT,
        id: 'image-unknown',
        label: 'Image',
        category: 'image',
        defaultViewer: 'image-preview',
      };
    }
    if (
      mimeType.startsWith('text/') ||
      mimeType === 'application/javascript' ||
      mimeType === 'application/typescript'
    ) {
      return {
        ...UNKNOWN_FORMAT,
        id: 'text-unknown',
        label: 'Text',
        category: 'text',
        defaultViewer: 'plain-text',
        monacoLanguage: 'plaintext',
      };
    }
  }
  return UNKNOWN_FORMAT;
}

/**
 * `usePathResolver` needs to decide synchronously (before the stat
 * round-trip completes) whether to pre-fetch text content. This is
 * the registry-driven version of the old `renderAs === 'markdown'`
 * shortcut.
 */
export function shouldPrefetchAsText(format: FileFormat): boolean {
  return format.category === 'markdown';
}

/**
 * Categories whose content should be loaded as text and rendered
 * via the `monaco-code` / `markdown-editor` / `csv-table` family. Used by
 * `EditorArea` to decide when to populate `markdownContent`.
 */
export function isTextLikeCategory(format: FileFormat): boolean {
  return (
    format.category === 'markdown' ||
    format.category === 'text' ||
    format.category === 'code' ||
    format.defaultViewer === 'csv-table' ||
    (format.category === 'data' && format.defaultViewer === 'monaco-code')
  );
}

export { FILE_FORMATS, UNKNOWN_FORMAT };
export type { FileFormat, ViewerId, GenericViewerId, SpecialViewerId } from './types';
export type { FileCategory, IngestStrategy } from './types';
