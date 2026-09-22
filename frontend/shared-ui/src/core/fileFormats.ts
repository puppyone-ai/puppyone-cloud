import registryJson from "./fileFormats.json";

export type FileCategory =
  | "image"
  | "audio"
  | "video"
  | "markdown"
  | "text"
  | "code"
  | "data"
  | "document"
  | "archive"
  | "binary";

export type GenericViewerId =
  | "markdown-editor"
  | "plain-text"
  | "monaco-code"
  | "csv-table"
  | "html-artifact"
  | "image-preview"
  | "audio-preview"
  | "video-preview"
  | "pdf-preview"
  | "office-preview"
  | "binary-placeholder";

export type SpecialViewerId = "json-table";

export type ViewerId = GenericViewerId | SpecialViewerId;

export type IngestStrategy =
  | "raw"
  | "parse-text"
  | "parse-structured"
  | "ocr";

export interface FileFormat {
  id: string;
  label: string;
  filenames?: string[];
  filenamePatterns?: string[];
  extensions?: string[];
  mimeTypes?: string[];
  category: FileCategory;
  defaultViewer: ViewerId;
  availableViewers?: ViewerId[];
  editable: boolean;
  ingestStrategy: IngestStrategy;
  monacoLanguage?: string;
}

export type FileSemanticKind =
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
  | "file";

export type FilePreviewKind =
  | "markdown"
  | "json"
  | "text"
  | "html"
  | "image"
  | "audio"
  | "video"
  | "pdf"
  | "placeholder";

export interface ResolveInput {
  name?: string | null;
  mimeType?: string | null;
}

type FileFormatRegistryData = {
  formats: FileFormat[];
  unknownFormat: FileFormat;
};

const registry = registryJson as FileFormatRegistryData;

export const FILE_FORMATS: FileFormat[] = registry.formats;
export const UNKNOWN_FORMAT: FileFormat = registry.unknownFormat;

const FILENAME_INDEX = new Map<string, FileFormat>();
const EXTENSION_INDEX = new Map<string, FileFormat>();
const MIME_INDEX = new Map<string, FileFormat>();
const FILENAME_PATTERNS: Array<{ regex: RegExp; format: FileFormat }> = [];

for (const format of FILE_FORMATS) {
  for (const filename of format.filenames ?? []) {
    FILENAME_INDEX.set(filename.toLowerCase(), format);
  }
  for (const pattern of format.filenamePatterns ?? []) {
    FILENAME_PATTERNS.push({
      regex: globPatternToRegExp(pattern.toLowerCase()),
      format,
    });
  }
  for (const extension of format.extensions ?? []) {
    EXTENSION_INDEX.set(extension.toLowerCase(), format);
  }
  for (const mimeType of format.mimeTypes ?? []) {
    MIME_INDEX.set(mimeType.toLowerCase(), format);
  }
}

export function resolveFileFormat(input: ResolveInput): FileFormat {
  const { name, mimeType } = input;

  if (name) {
    const base = basename(name).toLowerCase();
    const byName = FILENAME_INDEX.get(base);
    if (byName) return byName;

    const byExtension = matchExtension(name);
    if (byExtension) return byExtension;

    const byPattern = matchFilenamePattern(name);
    if (byPattern) return byPattern;
  }

  if (mimeType) {
    const normalizedMime = mimeType.toLowerCase().split(";")[0].trim();
    const byMime = MIME_INDEX.get(normalizedMime);
    if (byMime) return byMime;

    if (normalizedMime.startsWith("image/")) {
      return {
        ...UNKNOWN_FORMAT,
        id: "image-unknown",
        label: "Image",
        category: "image",
        defaultViewer: "image-preview",
      };
    }

    if (
      normalizedMime.startsWith("text/") ||
      normalizedMime === "application/javascript" ||
      normalizedMime === "application/typescript"
    ) {
      return {
        ...UNKNOWN_FORMAT,
        id: "text-unknown",
        label: "Text",
        category: "text",
        defaultViewer: "plain-text",
        monacoLanguage: "plaintext",
      };
    }
  }

  return UNKNOWN_FORMAT;
}

export function isKnownFileFormat(format: FileFormat): boolean {
  return format.id !== UNKNOWN_FORMAT.id;
}

export function getFileSemanticKind(
  name: string,
  type?: string | null,
  mimeType?: string | null,
): FileSemanticKind {
  const format = resolveFileFormat({ name, mimeType });
  if (isKnownFileFormat(format)) return getSemanticKindForFormat(format);
  if (isFileSemanticKind(type)) return type;
  return "file";
}

export function getFilePreviewKind(
  name: string,
  type?: string | null,
  mimeType?: string | null,
): FilePreviewKind {
  const format = resolveFileFormat({ name, mimeType });
  if (isKnownFileFormat(format)) return getPreviewKindForFormat(format);
  return getPreviewKindForSemanticType(type) ?? "placeholder";
}

export function isTextLikeFile(
  name: string,
  type?: string | null,
  mimeType?: string | null,
): boolean {
  const format = resolveFileFormat({ name, mimeType });
  if (isKnownFileFormat(format)) return isTextLikeFileFormat(format);
  return getPreviewKindForSemanticType(type) === "text"
    || type === "markdown"
    || type === "json"
    || type === "html";
}

export function isTextLikeFileFormat(format: FileFormat): boolean {
  return (
    format.category === "markdown" ||
    format.category === "text" ||
    format.category === "code" ||
    format.defaultViewer === "csv-table" ||
    (format.category === "data" && format.defaultViewer === "monaco-code")
  );
}

export function getPreviewKindForFormat(format: FileFormat): FilePreviewKind {
  if (format.id === "json" || format.id === "jsonl") return "json";

  switch (format.defaultViewer) {
    case "markdown-editor":
      return "markdown";
    case "plain-text":
    case "monaco-code":
    case "csv-table":
    case "json-table":
      return "text";
    case "html-artifact":
      return "html";
    case "image-preview":
      return "image";
    case "audio-preview":
      return "audio";
    case "video-preview":
      return "video";
    case "pdf-preview":
      return "pdf";
    case "office-preview":
      return "placeholder";
    case "binary-placeholder":
    default:
      return "placeholder";
  }
}

export function getSemanticKindForFormat(format: FileFormat): FileSemanticKind {
  if (format.id === "json" || format.id === "jsonl") return "json";

  switch (format.defaultViewer) {
    case "markdown-editor":
      return "markdown";
    case "html-artifact":
      return "html";
    case "image-preview":
      return "image";
    case "audio-preview":
      return "audio";
    case "video-preview":
      return "video";
    case "pdf-preview":
      return "pdf";
    case "csv-table":
      return "spreadsheet";
    default:
      break;
  }

  switch (format.category) {
    case "markdown":
      return "markdown";
    case "image":
      return "image";
    case "audio":
      return "audio";
    case "video":
      return "video";
    case "archive":
      return "archive";
    case "document":
      return format.id === "xlsx" ? "spreadsheet" : "document";
    case "binary":
      return "binary";
    case "text":
      return "text";
    case "code":
    case "data":
      return "code";
    default:
      return "file";
  }
}

export function getPreferredMimeType(name: string): string | null {
  const format = resolveFileFormat({ name });
  return format.mimeTypes?.[0] ?? null;
}

export function getMatchedExtension(name: string): string | null {
  const lower = basename(name).toLowerCase();
  const lastDot = lower.lastIndexOf(".");
  if (lastDot < 0) return null;

  const secondLastDot = lower.lastIndexOf(".", lastDot - 1);
  if (secondLastDot >= 0) {
    const compound = lower.slice(secondLastDot);
    if (EXTENSION_INDEX.has(compound)) return compound.slice(1);
  }

  return lower.slice(lastDot + 1);
}

function getPreviewKindForSemanticType(type?: string | null): FilePreviewKind | null {
  switch (type) {
    case "markdown":
      return "markdown";
    case "json":
      return "json";
    case "html":
      return "html";
    case "image":
      return "image";
    case "audio":
      return "audio";
    case "video":
      return "video";
    case "pdf":
      return "pdf";
    case "code":
    case "text":
    case "spreadsheet":
      return "text";
    default:
      return null;
  }
}

function matchExtension(name: string): FileFormat | null {
  const lower = basename(name).toLowerCase();
  const lastDot = lower.lastIndexOf(".");
  if (lastDot < 0) return null;

  const secondLastDot = lower.lastIndexOf(".", lastDot - 1);
  if (secondLastDot >= 0) {
    const compound = lower.slice(secondLastDot);
    const compoundMatch = EXTENSION_INDEX.get(compound);
    if (compoundMatch) return compoundMatch;
  }

  return EXTENSION_INDEX.get(lower.slice(lastDot)) ?? null;
}

function matchFilenamePattern(name: string): FileFormat | null {
  const normalized = name.replace(/\\/g, "/").toLowerCase();
  const base = basename(normalized);

  for (const { regex, format } of FILENAME_PATTERNS) {
    if (regex.test(normalized) || regex.test(base)) return format;
  }

  return null;
}

function globPatternToRegExp(pattern: string): RegExp {
  let source = "^";

  for (let index = 0; index < pattern.length; index += 1) {
    const char = pattern[index];
    const next = pattern[index + 1];
    const afterNext = pattern[index + 2];

    if (char === "*" && next === "*" && afterNext === "/") {
      source += "(?:.*/)?";
      index += 2;
      continue;
    }

    if (char === "*" && next === "*") {
      source += ".*";
      index += 1;
      continue;
    }

    if (char === "*") {
      source += "[^/]*";
      continue;
    }

    if (char === "?") {
      source += "[^/]";
      continue;
    }

    source += escapeRegExp(char);
  }

  return new RegExp(`${source}$`);
}

function escapeRegExp(value: string): string {
  return value.replace(/[\\^$.*+?()[\]{}|]/g, "\\$&");
}

function basename(path: string): string {
  const lastSlash = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return lastSlash >= 0 ? path.slice(lastSlash + 1) : path;
}

function isFileSemanticKind(value: string | null | undefined): value is FileSemanticKind {
  return Boolean(value && [
    "folder",
    "markdown",
    "json",
    "html",
    "image",
    "audio",
    "pdf",
    "video",
    "spreadsheet",
    "archive",
    "document",
    "binary",
    "code",
    "text",
    "file",
  ].includes(value));
}
