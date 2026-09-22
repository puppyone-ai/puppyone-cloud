'use client';

import type { MarkdownViewMode } from '@/components/editors/markdown';
import { useCommitUpdates } from '@/contexts/VersionWebSocketContext';
import { readFile, stat } from '@/lib/contentTreeApi';
import { isTextLikeCategory, resolveFormat, UNKNOWN_FORMAT } from '@/lib/fileFormats';
import { isFolderType } from '@/lib/nodeTypeConfig';
import { useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Seed the `activeNodeType` (the *node* type, not the file format)
 * for an instant render before stat returns. Driven by the file-format
 * registry: any markdown format → 'markdown' nodeType, any JSON format
 * → 'json' nodeType, everything else → 'file'. Folders are handled
 * by `typeHint`, so this never returns 'folder'.
 */
function inferTypeFromName(name: string): string {
  const fmt = resolveFormat({ name, mimeType: null });
  if (fmt.id === 'markdown') return 'markdown';
  if (fmt.id === 'json') return 'json';
  return 'file';
}

function buildBreadcrumbs(segments: string[]): Array<{ id: string; name: string }> {
  return segments.map((seg, i) => ({
    id: segments.slice(0, i + 1).join('/'),
    name: seg,
  }));
}

function applyFileState(
  fullPath: string,
  resolvedType: string,
  segments: string[],
  breadcrumbs: Array<{ id: string; name: string }>,
  setters: {
    setActiveNodeId: (v: string) => void;
    setActiveNodeType: (v: string) => void;
    setActivePreviewType: (v: string | null) => void;
    setCurrentFolderPath: (v: string | null) => void;
    setFolderBreadcrumbs: (v: Array<{ id: string; name: string }>) => void;
  },
) {
  if (isFolderType(resolvedType)) {
    setters.setCurrentFolderPath(fullPath);
    setters.setFolderBreadcrumbs(breadcrumbs);
    setters.setActiveNodeId('');
    setters.setActiveNodeType('');
  } else {
    setters.setActiveNodeId(fullPath);
    setters.setActiveNodeType(resolvedType);
    if (segments.length > 1) {
      setters.setCurrentFolderPath(segments.slice(0, -1).join('/'));
      setters.setFolderBreadcrumbs(breadcrumbs.slice(0, -1));
    } else {
      setters.setCurrentFolderPath(null);
      setters.setFolderBreadcrumbs([]);
    }
  }
  setters.setActivePreviewType(null);
}

function safeDecode(s: string): string {
  try { return decodeURIComponent(s); } catch { return s; }
}

function treeContentToEditorText(fileContent: Awaited<ReturnType<typeof readFile>>): string {
  if (typeof fileContent.content_text === 'string') return fileContent.content_text;
  if (fileContent.content !== null && fileContent.content !== undefined) {
    try {
      return JSON.stringify(fileContent.content, null, 2);
    } catch {
      return String(fileContent.content);
    }
  }
  return '';
}

export function usePathResolver(
  projectId: string,
  rawPath: string[],
  typeHintOverride?: string,
  onResolved?: () => void,
) {
  const path = rawPath.map(safeDecode);
  const searchParams = useSearchParams();
  const [currentFolderPath, setCurrentFolderPath] = useState<string | null>(null);
  const [folderBreadcrumbs, setFolderBreadcrumbs] = useState<Array<{ id: string; name: string }>>([]);
  const [isResolvingPath, setIsResolvingPath] = useState(path.length > 0);

  const [activeNodeId, setActiveNodeId] = useState<string>('');
  const [activeNodeType, setActiveNodeType] = useState<string>('');
  const [activePreviewType, setActivePreviewType] = useState<string | null>(null);
  const [activeMimeType, setActiveMimeType] = useState<string | null>(null);

  // `textContent` holds the raw UTF-8 contents of the active file
  // when its file format is text-like (markdown / code / yaml / csv /
  // plaintext). It's empty when the active node is a folder, an
  // image, a PDF, or another binary.
  const [textContent, setTextContent] = useState<string>('');
  const [isLoadingText, setIsLoadingText] = useState(false);
  const [markdownViewMode, setMarkdownViewMode] = useState<MarkdownViewMode>('wysiwyg');

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const pathKey = path.join('/');
  const typeHint = typeHintOverride ?? searchParams?.get('type') ?? '';
  const [revision, setRevision] = useState(0);
  const identity = JSON.stringify([projectId, pathKey, typeHint]);
  const [readIdentity, setReadIdentity] = useState<string | null>(null);
  const [readError, setReadError] = useState<Error | null>(null);
  const retryRead = useCallback(() => setRevision(value => value + 1), []);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useCommitUpdates(event => {
    if (!pathKey || !event.changed_files?.some(changed => changed === pathKey || pathKey.startsWith(`${changed}/`))) return;
    if (refreshTimer.current) return;
    refreshTimer.current = setTimeout(() => { refreshTimer.current = undefined; setRevision(value => value + 1); }, 100);
  });
  useEffect(() => () => { clearTimeout(refreshTimer.current); refreshTimer.current = undefined; }, [projectId, pathKey]);

  useEffect(() => {
    let cancelled = false;
    setReadIdentity(identity);
    setReadError(null);
    const controller = new AbortController();
    const setters = {
      setActiveNodeId,
      setActiveNodeType,
      setActivePreviewType,
      setCurrentFolderPath,
      setFolderBreadcrumbs,
    };

    async function resolve() {
      if (path.length === 0) {
        setIsResolvingPath(false);
        setIsLoadingText(false);
        onResolved?.();
        setCurrentFolderPath(null);
        setFolderBreadcrumbs([]);
        setActiveNodeId('');
        setActiveNodeType('');
        setActivePreviewType(null);
        setActiveMimeType(null);
        setTextContent('');
        return;
      }

      const fullPath = path.join('/');
      const breadcrumbs = buildBreadcrumbs(path);
      const fileName = path[path.length - 1] ?? '';

      // Resolve the file format up front from the filename alone —
      // extension is enough for ~99% of files, so we don't need to
      // wait for the stat round-trip to know what to fetch / render.
      const fmtFromName = resolveFormat({ name: fileName, mimeType: null });

      // When a type hint is available (sidebar click), render immediately
      // without waiting for the stat round-trip.
      if (typeHint) {
        applyFileState(fullPath, typeHint, path, breadcrumbs, setters);
        onResolved?.();
        setActiveMimeType(null);
        // Page-level spinner needs to stay up only when:
        //   - typeHint says it's a file, AND
        //   - extension didn't match anything in the registry, AND
        //   - we still need stat to give us a server-side mime
        // Otherwise the registry already knows what viewer to mount,
        // and the viewer's internal loader takes over.
        const isFolderHint = isFolderType(typeHint);
        const needsStatToDispatch =
          !isFolderHint && fmtFromName.id === UNKNOWN_FORMAT.id;
        setIsResolvingPath(needsStatToDispatch);
      } else {
        setIsResolvingPath(true);
      }

      // Pre-fetch the text content in parallel with stat for any
      // text-like format. The previous version only did this for
      // markdown — but code/yaml/csv/plaintext all want it too.
      const inferredType = typeHint || inferTypeFromName(fileName);
      const shouldReadFile =
        !isFolderType(inferredType) && isTextLikeCategory(fmtFromName);
      if (shouldReadFile) {
        setIsLoadingText(true);
      } else {
        setIsLoadingText(false);
      }

      const statPromise = stat(projectId, fullPath, controller.signal);
      let bodyError: unknown;
      const readFilePromise = shouldReadFile
        ? readFile(projectId, fullPath, controller.signal).catch(error => { bodyError = error; return null; })
        : Promise.resolve(null);

      const statResult = await statPromise;
      if (cancelled) return;

      let resolvedType: string;
      let resolvedMime: string | null = null;
      let exists = true;

      if (statResult) {
        exists = statResult.exists;
        resolvedType = statResult.type || inferredType;
        resolvedMime = statResult.mime_type ?? null;
      } else {
        resolvedType = inferredType;
      }

      if (!exists) {
        // If we already rendered the editor via typeHint, keep it — don't
        // fall back to root just because stat lost the race against an
        // in-flight write. (Without typeHint there's no pending render to
        // preserve, so resetting is correct.)
        if (!typeHint) {
          onResolved?.();
          setCurrentFolderPath(null);
          setFolderBreadcrumbs([]);
          setActiveNodeId('');
          setActiveNodeType('');
          setActivePreviewType(null);
          setActiveMimeType(null);
          setTextContent('');
          setIsLoadingText(false);
        }
        setIsResolvingPath(false);
        setIsLoadingText(false);
        setReadError(new Error('This file or folder is no longer available.'));
        return;
      }

      applyFileState(fullPath, resolvedType, path, breadcrumbs, setters);
      setActiveMimeType(resolvedMime);
      onResolved?.();
      // Metadata can paint before the (potentially much slower) file body.
      setIsResolvingPath(false);

      // Re-resolve format with mime now that we have it — covers the
      // "unknown extension but server-detected mime" edge case.
      const fmtFinal = resolveFormat({ name: fileName, mimeType: resolvedMime });
      const finalNeedsText =
        !isFolderType(resolvedType) && isTextLikeCategory(fmtFinal);

      if (finalNeedsText) {
        const fileContent = await readFilePromise;
        if (cancelled) return;
        if (fileContent !== null) {
          setTextContent(treeContentToEditorText(fileContent));
          setIsLoadingText(false);
        } else {
          // A failed speculative body read is an error, not empty text and not
          // a reason to send the same expensive read a second time.
          if (shouldReadFile) throw bodyError ?? new Error('Could not load file.');
          // Fallback: fetch now (e.g. extension unknown, mime arrived as
          // text/* via stat).
          setIsLoadingText(true);
          try {
            const content = await readFile(projectId, fullPath, controller.signal);
            if (cancelled) return;
            setTextContent(treeContentToEditorText(content));
          } catch (readErr) {
            if (cancelled) return;
            setReadError(readErr instanceof Error ? readErr : new Error('Could not load file.'));
          } finally {
            if (!cancelled) setIsLoadingText(false);
          }
        }
      } else {
        setTextContent('');
        setIsLoadingText(false);
      }

      if (!cancelled) setIsResolvingPath(false);
    }

    resolve().catch((err) => {
      if (cancelled) return;
      setReadError(err instanceof Error ? err : new Error('Could not load file.'));
      const fullPath = path.join('/');
      const breadcrumbs = buildBreadcrumbs(path);
      const guessedType = typeHint || inferTypeFromName(path[path.length - 1] ?? '');
      applyFileState(fullPath, guessedType, path, breadcrumbs, setters);
      onResolved?.();
      setIsLoadingText(false);
      setIsResolvingPath(false);
    });

    return () => { cancelled = true; controller.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, pathKey, typeHint, onResolved, revision]);

  return {
    currentFolderId: currentFolderPath,
    setCurrentFolderId: setCurrentFolderPath,
    folderBreadcrumbs,
    isResolvingPath: readIdentity !== identity || isResolvingPath,
    readError: readIdentity === identity ? readError : null,
    retryRead,
    activeNodeId,
    activeNodeType,
    activePreviewType,
    activeMimeType,
    textContent,
    setTextContent,
    isLoadingText: readIdentity !== identity || isLoadingText,
    markdownViewMode,
    setMarkdownViewMode,
  };
}
