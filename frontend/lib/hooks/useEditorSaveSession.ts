'use client';

import { useCallback, useMemo } from 'react';
import { writeFile, type NodeType } from '@/lib/contentTreeApi';
import {
  useManualSave,
  type SaveStatus,
} from '@/lib/hooks/useManualSave';

export type EditorSaveNodeType = Extract<NodeType, 'file' | 'json' | 'markdown'>;

export interface UseEditorSaveSessionOptions {
  readonly projectId: string;
  readonly filePath: string;
  readonly serverContent: string;
  readonly nodeType: EditorSaveNodeType;
  readonly saveContent?: (content: string) => Promise<void>;
  readonly skipDraftRestore?: boolean;
  readonly isContentReady?: boolean;
}

export interface EditorSaveSession {
  readonly content: string;
  readonly onChange: (next: string) => void;
  readonly status: SaveStatus;
  readonly dirty: boolean;
  readonly error: string | null;
  readonly save: () => Promise<void>;
  readonly discard: () => void;
  readonly hasRestoredDraft: boolean;
  readonly acknowledgeRestoredDraft: () => void;
}

function validateEditorContent(
  content: string,
  filePath: string,
  nodeType: EditorSaveNodeType,
): void {
  if (nodeType !== 'json') return;
  const lowerPath = filePath.toLowerCase();
  if (lowerPath.endsWith('.jsonc') || lowerPath.endsWith('.json5')) return;
  try {
    JSON.parse(content);
  } catch {
    throw new Error('Invalid JSON. Fix the syntax before saving.');
  }
}

export function useEditorSaveSession({
  projectId,
  filePath,
  serverContent,
  nodeType,
  saveContent,
  skipDraftRestore = false,
  isContentReady = true,
}: UseEditorSaveSessionOptions): EditorSaveSession {
  const fileKey = useMemo(
    () => `${nodeType}:${projectId}:${filePath || '(none)'}`,
    [nodeType, projectId, filePath],
  );

  const stringIdentity = useCallback((value: string) => value, []);
  const stringEquals = useCallback((a: string, b: string) => a === b, []);

  const save = useCallback(
    async (snapshot: string) => {
      if (!isContentReady) throw new Error('Cannot save before file content has loaded.');
      if (!filePath) {
        throw new Error('Cannot save: no active editor target');
      }
      validateEditorContent(snapshot, filePath, nodeType);
      if (saveContent) {
        await saveContent(snapshot);
        return;
      }
      await writeFile(projectId, filePath, snapshot, nodeType);
    },
    [filePath, nodeType, projectId, saveContent, isContentReady],
  );

  const inner = useManualSave<string>({
    fileKey,
    serverContent,
    isEqual: stringEquals,
    serialize: stringIdentity,
    deserialize: stringIdentity,
    save,
    skipDraftRestore: skipDraftRestore || !filePath,
  });

  return {
    content: inner.draft,
    onChange: inner.setDraft,
    status: inner.status,
    dirty: inner.dirty,
    error: inner.error,
    save: inner.save,
    discard: inner.discard,
    hasRestoredDraft: inner.hasRestoredDraft,
    acknowledgeRestoredDraft: inner.acknowledgeRestoredDraft,
  };
}
