import type {
  MarkdownBacklink,
  MarkdownBacklinkReference,
  MarkdownLinkGraph,
  MarkdownWikiLinkResolvedTarget,
} from "../../viewerTypes";
import { findMarkdownLinkTokens, isExternalMarkdownHref } from "./markdownLinkModel";
import { findWikiLinkTokens, splitWikiLinkTarget } from "./wikiLinkModel";

export type MarkdownLinkGraphDocument = {
  path: string;
  name: string;
  content?: string | null;
};

export type CreateMarkdownLinkGraphOptions = {
  isIndexing?: boolean;
  onOpenPath?: (path: string) => void;
  onOpenCandidatePaths?: (paths: readonly string[]) => void;
  onOpenExternalUrl?: (href: string) => void;
};

type IndexedDocument = {
  path: string;
  name: string;
  title: string;
  normalizedPath: string;
  normalizedPathWithoutExtension: string;
  content: string | null;
};

export function createMarkdownLinkGraph(
  documents: readonly MarkdownLinkGraphDocument[],
  options: CreateMarkdownLinkGraphOptions = {},
): MarkdownLinkGraph {
  const indexedDocuments = documents.map(toIndexedDocument).sort((left, right) => left.path.localeCompare(right.path));
  const pathIndex = createPathIndex(indexedDocuments);
  const titleIndex = createTitleIndex(indexedDocuments);
  const backlinksByTargetPath = createBacklinkIndex(indexedDocuments, pathIndex, titleIndex);

  const resolveWikiLink = (sourcePath: string, target: string): MarkdownWikiLinkResolvedTarget => {
    const resolved = resolveWikiLinkTarget(indexedDocuments, pathIndex, titleIndex, sourcePath, target);
    return resolved;
  };

  return {
    documentCount: indexedDocuments.length,
    indexedDocumentCount: indexedDocuments.filter((document) => document.content !== null).length,
    isIndexing: Boolean(options.isIndexing),
    resolveWikiLink,
    resolveMarkdownLink(sourcePath, href) {
      const target = normalizeMarkdownHrefTarget(href);
      if (!target) return null;
      return resolveWikiLinkTarget(indexedDocuments, pathIndex, titleIndex, sourcePath, target);
    },
    openWikiLink(target) {
      if (target.exists && target.path) {
        options.onOpenPath?.(target.path);
        return;
      }

      if (target.candidatePaths && target.candidatePaths.length > 0) {
        options.onOpenCandidatePaths?.(target.candidatePaths);
      }
    },
    openPath(path) {
      options.onOpenPath?.(path);
    },
    openExternalUrl(href) {
      options.onOpenExternalUrl?.(href);
    },
    getBacklinks(path) {
      return backlinksByTargetPath.get(normalizeDataPath(path)) ?? [];
    },
  };
}

function createBacklinkIndex(
  documents: readonly IndexedDocument[],
  pathIndex: Map<string, IndexedDocument>,
  titleIndex: Map<string, IndexedDocument[]>,
): Map<string, MarkdownBacklink[]> {
  const backlinksByTargetPath = new Map<string, Map<string, MarkdownBacklink>>();

  for (const document of documents) {
    if (!document.content) continue;

    const tokens = findWikiLinkTokens(document.content);
    for (const token of tokens) {
      const target = resolveWikiLinkTarget(documents, pathIndex, titleIndex, document.path, token.target);
      if (!target.exists || !target.path) continue;

      const normalizedTargetPath = normalizeDataPath(target.path);
      const sourceMap = getOrCreateMap(backlinksByTargetPath, normalizedTargetPath);
      const existing = sourceMap.get(document.path);
      const reference = createBacklinkReference(document.content, token.from, token.target, token.label);

      if (existing) {
        existing.count += 1;
        existing.references.push(reference);
        continue;
      }

      sourceMap.set(document.path, {
        sourcePath: document.path,
        sourceName: document.name,
        count: 1,
        references: [reference],
      });
    }

    const markdownLinkTokens = findMarkdownLinkTokens(document.content);
    for (const token of markdownLinkTokens) {
      const normalizedTarget = normalizeMarkdownHrefTarget(token.href);
      if (!normalizedTarget) continue;

      const target = resolveWikiLinkTarget(documents, pathIndex, titleIndex, document.path, normalizedTarget);
      if (!target.exists || !target.path) continue;

      const normalizedTargetPath = normalizeDataPath(target.path);
      const sourceMap = getOrCreateMap(backlinksByTargetPath, normalizedTargetPath);
      const existing = sourceMap.get(document.path);
      const reference = createBacklinkReference(document.content, token.from, token.href, token.label);

      if (existing) {
        existing.count += 1;
        existing.references.push(reference);
        continue;
      }

      sourceMap.set(document.path, {
        sourcePath: document.path,
        sourceName: document.name,
        count: 1,
        references: [reference],
      });
    }
  }

  const result = new Map<string, MarkdownBacklink[]>();
  for (const [targetPath, sourceMap] of backlinksByTargetPath.entries()) {
    result.set(
      targetPath,
      Array.from(sourceMap.values()).sort((left, right) => left.sourcePath.localeCompare(right.sourcePath)),
    );
  }

  return result;
}

function normalizeMarkdownHrefTarget(href: string): string | null {
  const value = href.trim();
  if (!value || value.startsWith("#") || isExternalMarkdownHref(value)) return null;

  const withoutQuery = value.split("?")[0] ?? value;
  const hashIndex = withoutQuery.indexOf("#");
  const rawPath = hashIndex === -1 ? withoutQuery : withoutQuery.slice(0, hashIndex);
  const rawHeading = hashIndex === -1 ? "" : withoutQuery.slice(hashIndex + 1);
  const decodedPath = decodeMarkdownHrefPath(rawPath);
  const decodedHeading = decodeMarkdownHrefPath(rawHeading);
  if (!decodedPath && !decodedHeading) return null;
  return decodedHeading ? `${decodedPath}#${decodedHeading}` : decodedPath;
}

function decodeMarkdownHrefPath(value: string): string {
  const normalized = value.replace(/\\/g, "/").trim();
  try {
    return decodeURIComponent(normalized);
  } catch {
    return normalized;
  }
}

function createBacklinkReference(
  content: string,
  offset: number,
  target: string,
  label: string,
): MarkdownBacklinkReference {
  const before = content.slice(0, offset);
  const lineNumber = before.split("\n").length;
  const lineStart = content.lastIndexOf("\n", Math.max(0, offset - 1)) + 1;
  const lineEnd = content.indexOf("\n", offset);
  const rawLine = content.slice(lineStart, lineEnd === -1 ? content.length : lineEnd);

  return {
    lineNumber,
    lineText: rawLine.trim(),
    target,
    label,
  };
}

function resolveWikiLinkTarget(
  documents: readonly IndexedDocument[],
  pathIndex: Map<string, IndexedDocument>,
  titleIndex: Map<string, IndexedDocument[]>,
  sourcePath: string,
  target: string,
): MarkdownWikiLinkResolvedTarget {
  const parts = splitWikiLinkTarget(target);
  const rawTargetPath = parts.targetPath.trim();
  const targetPath = normalizeDataPath(rawTargetPath);
  const sourceDocument = pathIndex.get(normalizeDataPath(sourcePath)) ?? null;
  const sourceFolder = sourceDocument ? getParentPath(sourceDocument.path) : getParentPath(sourcePath);

  if (!targetPath && parts.heading && sourceDocument) {
    return createResolvedTarget(sourceDocument, target, parts.heading, false);
  }

  const pathCandidates = createPathCandidates(rawTargetPath, sourceFolder);
  for (const candidate of pathCandidates) {
    const exactMatch = pathIndex.get(candidate);
    if (exactMatch) return createResolvedTarget(exactMatch, target, parts.heading, false);
  }

  const titleMatches = titleIndex.get(normalizeLookupKey(stripKnownFileExtension(getBaseName(targetPath)))) ?? [];
  if (titleMatches.length === 1) {
    return createResolvedTarget(titleMatches[0], target, parts.heading, false);
  }

  if (titleMatches.length > 1) {
    const nearest = pickNearestDocument(titleMatches, sourceFolder);
    return createResolvedTarget(nearest, target, parts.heading, true);
  }

  const candidatePaths = createNavigationPathCandidates(rawTargetPath, sourceFolder);
  return {
    exists: false,
    ambiguous: false,
    path: null,
    candidatePaths,
    name: getBaseName(targetPath) || target,
    displayName: getDisplayNameFromTarget(target),
    target,
    heading: parts.heading,
  };
}

function createResolvedTarget(
  document: IndexedDocument,
  target: string,
  heading: string | null,
  ambiguous: boolean,
): MarkdownWikiLinkResolvedTarget {
  return {
    exists: true,
    ambiguous,
    path: document.path,
    candidatePaths: [document.path],
    name: document.name,
    displayName: document.title,
    target,
    heading,
  };
}

function createPathCandidates(targetPath: string, sourceFolder: string | null): string[] {
  if (!targetPath) return [];

  const candidates: string[] = [];
  const normalizedTarget = normalizeDataPath(targetPath);
  const isRooted = targetPath.startsWith("/");

  if (!isRooted && sourceFolder) {
    addPathCandidate(candidates, joinDataPath(sourceFolder, targetPath));
  }

  addPathCandidate(candidates, normalizedTarget);
  return candidates;
}

function addPathCandidate(candidates: string[], path: string) {
  const normalizedPath = normalizeDataPath(path);
  if (!normalizedPath) return;

  const variants = [
    normalizedPath,
    stripKnownFileExtension(normalizedPath),
    hasMarkdownExtension(normalizedPath) ? normalizedPath : `${normalizedPath}.md`,
    hasMarkdownExtension(normalizedPath) ? normalizedPath : `${normalizedPath}.markdown`,
  ];

  for (const variant of variants) {
    const normalizedVariant = normalizeLookupKey(normalizeDataPath(variant));
    if (normalizedVariant && !candidates.includes(normalizedVariant)) candidates.push(normalizedVariant);
  }
}

function createNavigationPathCandidates(targetPath: string, sourceFolder: string | null): string[] {
  if (!targetPath) return [];

  const candidates: string[] = [];
  const normalizedTarget = normalizeDataPath(targetPath);
  const isRooted = targetPath.startsWith("/");

  if (!isRooted && sourceFolder) {
    addNavigationPathCandidate(candidates, `${sourceFolder}/${targetPath}`);
  }

  addNavigationPathCandidate(candidates, normalizedTarget);
  return candidates;
}

function addNavigationPathCandidate(candidates: string[], path: string) {
  const normalizedPath = normalizeDataPath(path);
  if (!normalizedPath) return;

  const variants = [
    normalizedPath,
    hasKnownFileExtension(normalizedPath) ? null : `${normalizedPath}.md`,
    hasKnownFileExtension(normalizedPath) ? null : `${normalizedPath}.markdown`,
    stripKnownFileExtension(normalizedPath),
  ].filter((value): value is string => Boolean(value));

  for (const variant of variants) {
    const normalizedVariant = normalizeDataPath(variant);
    if (!normalizedVariant) continue;
    if (!candidates.some((candidate) => normalizeLookupKey(candidate) === normalizeLookupKey(normalizedVariant))) {
      candidates.push(normalizedVariant);
    }
  }
}

function createPathIndex(documents: readonly IndexedDocument[]): Map<string, IndexedDocument> {
  const index = new Map<string, IndexedDocument>();
  for (const document of documents) {
    index.set(document.normalizedPath, document);
    index.set(document.normalizedPathWithoutExtension, document);
  }
  return index;
}

function createTitleIndex(documents: readonly IndexedDocument[]): Map<string, IndexedDocument[]> {
  const index = new Map<string, IndexedDocument[]>();
  for (const document of documents) {
    const titleKey = normalizeLookupKey(document.title);
    const current = index.get(titleKey);
    if (current) current.push(document);
    else index.set(titleKey, [document]);
  }
  return index;
}

function pickNearestDocument(documents: readonly IndexedDocument[], sourceFolder: string | null): IndexedDocument {
  if (!sourceFolder) return documents[0];
  return [...documents].sort((left, right) => {
    const leftScore = getPathProximityScore(left.path, sourceFolder);
    const rightScore = getPathProximityScore(right.path, sourceFolder);
    if (leftScore !== rightScore) return rightScore - leftScore;
    return left.path.localeCompare(right.path);
  })[0];
}

function getPathProximityScore(path: string, sourceFolder: string): number {
  const pathParts = path.split("/");
  const folderParts = sourceFolder.split("/");
  let score = 0;
  while (score < pathParts.length && score < folderParts.length && pathParts[score] === folderParts[score]) {
    score += 1;
  }
  return score;
}

function toIndexedDocument(document: MarkdownLinkGraphDocument): IndexedDocument {
  const normalizedPath = normalizeDataPath(document.path);
  const title = stripKnownFileExtension(document.name || getBaseName(document.path));

  return {
    path: document.path,
    name: document.name,
    title,
    normalizedPath: normalizeLookupKey(normalizedPath),
    normalizedPathWithoutExtension: normalizeLookupKey(stripKnownFileExtension(normalizedPath)),
    content: typeof document.content === "string" ? document.content : null,
  };
}

function getOrCreateMap<K, V>(map: Map<K, Map<string, V>>, key: K): Map<string, V> {
  const existing = map.get(key);
  if (existing) return existing;
  const next = new Map<string, V>();
  map.set(key, next);
  return next;
}

function getDisplayNameFromTarget(target: string): string {
  const parts = splitWikiLinkTarget(target);
  if (parts.targetPath) return stripKnownFileExtension(getBaseName(parts.targetPath));
  return parts.heading ?? target;
}

function normalizeDataPath(path: string): string {
  const normalized = path.replace(/\\/g, "/").replace(/^\/+/, "").replace(/\/+/g, "/").trim();
  const parts: string[] = [];

  for (const part of normalized.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      parts.pop();
      continue;
    }
    parts.push(part);
  }

  return parts.join("/");
}

function normalizeLookupKey(value: string): string {
  return normalizeDataPath(value).toLocaleLowerCase();
}

function hasMarkdownExtension(path: string): boolean {
  return /\.(?:md|markdown)$/i.test(path);
}

function hasKnownFileExtension(path: string): boolean {
  return /\.[^/.]+$/i.test(getBaseName(path));
}

function stripKnownFileExtension(path: string): string {
  return path.replace(/\.[^/.]+$/i, "");
}

function getBaseName(path: string): string {
  const normalizedPath = normalizeDataPath(path);
  if (!normalizedPath) return "";
  const lastSlash = normalizedPath.lastIndexOf("/");
  return lastSlash === -1 ? normalizedPath : normalizedPath.slice(lastSlash + 1);
}

function getParentPath(path: string | null): string | null {
  if (!path || !path.includes("/")) return null;
  return path.slice(0, path.lastIndexOf("/"));
}

function joinDataPath(folderPath: string, name: string): string {
  return `${folderPath}/${name}`;
}
