"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { EditorSaveButton, type SaveStatus } from "../EditorSaveButton";
import { PlainTextEditor } from "../PlainTextEditor";
import type { EditorMode, EditorSaveMode } from "../viewerTypes";

export type TextEditorFrameProps = {
  documentId: string;
  content: string;
  nodeName: string;
  defaultMode: EditorMode;
  canEdit: boolean;
  onSaveContent?: (content: string) => Promise<void>;
  hideSourceView: boolean;
  saveMode: EditorSaveMode;
  renderLive: (content: string, controls: { canEdit: boolean; onChange: (content: string) => void }) => ReactNode;
  renderSource?: (content: string, controls: { canEdit: boolean; onChange: (content: string) => void }) => ReactNode;
};

export function TextEditorFrame({
  documentId,
  content,
  nodeName,
  defaultMode,
  canEdit,
  onSaveContent,
  hideSourceView,
  saveMode,
  renderLive,
  renderSource,
}: TextEditorFrameProps) {
  const [mode, setMode] = useState<EditorMode>(hideSourceView ? "live" : defaultMode);
  const [draft, setDraft] = useState(content);
  const [persistedContent, setPersistedContent] = useState(content);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("clean");
  const [saveError, setSaveError] = useState<string | null>(null);
  const documentIdRef = useRef(documentId);
  const draftRef = useRef(draft);
  const persistedContentRef = useRef(persistedContent);
  const savingRef = useRef(false);
  const queuedAutoSaveRef = useRef<string | null>(null);
  const dirty = draft !== persistedContent;

  useLayoutEffect(() => {
    if (documentIdRef.current === documentId) return;

    documentIdRef.current = documentId;
    savingRef.current = false;
    queuedAutoSaveRef.current = null;
    draftRef.current = content;
    persistedContentRef.current = content;
    setMode(hideSourceView ? "live" : defaultMode);
    setDraft(content);
    setPersistedContent(content);
    setSaveStatus("clean");
    setSaveError(null);
  }, [content, defaultMode, documentId, hideSourceView]);

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  useEffect(() => {
    persistedContentRef.current = persistedContent;
  }, [persistedContent]);

  useLayoutEffect(() => {
    if (documentIdRef.current !== documentId) return;

    const previousPersistedContent = persistedContentRef.current;
    const hasLocalDraft = saveMode === "auto" &&
      draftRef.current !== previousPersistedContent &&
      draftRef.current !== content;

    setPersistedContent(content);
    persistedContentRef.current = content;
    setSaveError(null);

    if (hasLocalDraft) {
      setSaveStatus("dirty");
      return;
    }

    setDraft(content);
    draftRef.current = content;
    setSaveStatus("clean");
  }, [content, documentId, saveMode]);

  useEffect(() => {
    if (hideSourceView) setMode("live");
  }, [hideSourceView]);

  useEffect(() => {
    if (dirty) setSaveStatus((status) => (status === "saving" ? status : "dirty"));
    else if (saveStatus === "dirty" || saveStatus === "error") setSaveStatus("clean");
  }, [dirty, saveStatus]);

  const saveContent = async (contentToSave: string, automatic: boolean) => {
    if (!onSaveContent || contentToSave === persistedContentRef.current) return;

    if (savingRef.current) {
      if (automatic) queuedAutoSaveRef.current = contentToSave;
      return;
    }

    const saveDocumentId = documentIdRef.current;
    savingRef.current = true;
    setSaveStatus("saving");
    setSaveError(null);
    try {
      await onSaveContent(contentToSave);
      if (documentIdRef.current !== saveDocumentId) return;

      persistedContentRef.current = contentToSave;
      setPersistedContent(contentToSave);
      setSaveStatus(draftRef.current === contentToSave ? "saved" : "dirty");
      window.setTimeout(() => {
        if (documentIdRef.current !== saveDocumentId) return;
        setSaveStatus((status) => (status === "saved" ? "clean" : status));
      }, 1200);
    } catch (error) {
      if (documentIdRef.current !== saveDocumentId) return;
      setSaveStatus("error");
      setSaveError(error instanceof Error ? error.message : String(error));
    } finally {
      if (documentIdRef.current !== saveDocumentId) return;

      savingRef.current = false;
      if (automatic) {
        const queuedContent = queuedAutoSaveRef.current;
        queuedAutoSaveRef.current = null;
        if (queuedContent !== null && queuedContent !== persistedContentRef.current) {
          window.setTimeout(() => {
            void saveContent(draftRef.current, true);
          }, 0);
        }
      }
    }
  };

  const save = () => {
    void saveContent(draftRef.current, false);
  };

  useEffect(() => {
    if (saveMode !== "auto" || !dirty || !onSaveContent) return undefined;

    const timeoutId = window.setTimeout(() => {
      void saveContent(draftRef.current, true);
    }, 400);

    return () => window.clearTimeout(timeoutId);
  }, [dirty, draft, onSaveContent, saveMode]);

  return (
    <section className="editor-host">
      {saveMode === "manual" && (
        <div className="editor-save-overlay">
          <EditorSaveButton status={saveStatus} onSave={save} />
        </div>
      )}

      {saveError && <div className="editor-inline-error">{saveError}</div>}

      {mode === "live" ? (
        <div className="editor-live-surface">
          {renderLive(draft, { canEdit, onChange: setDraft })}
        </div>
      ) : renderSource ? (
        <div className="editor-live-surface">
          {renderSource(draft, { canEdit, onChange: setDraft })}
        </div>
      ) : (
        <PlainTextEditor
          content={draft}
          nodeName={nodeName}
          readOnly={!canEdit}
          onChange={canEdit ? setDraft : undefined}
        />
      )}

      {!hideSourceView && (
        <div className="editor-mode-toggle" aria-label="Editor mode">
          <button
            className={mode === "live" ? "active" : ""}
            type="button"
            onClick={() => setMode("live")}
            title="Live view"
            aria-label="Live view"
          >
            <PencilIcon />
          </button>
          <button
            className={mode === "source" ? "active" : ""}
            type="button"
            onClick={() => setMode("source")}
            title="Source code"
            aria-label="Source code"
          >
            <CodeIcon />
          </button>
        </div>
      )}
    </section>
  );
}

function PencilIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
    </svg>
  );
}

function CodeIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
    </svg>
  );
}
