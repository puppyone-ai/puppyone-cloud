"use client";

import { Annotation, Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { useEffect, useLayoutEffect, useRef } from "react";
import {
  markdownCodeMirrorBaseExtensions,
  markdownLivePreviewExtension,
} from "./markdownCodeMirrorExtensions";
import { markdownAiEditExtension } from "./markdownAiEditExtension";
import type { AiEditFile } from "../ai-edits/types";
import type { MarkdownHtmlTrustMode, MarkdownLinkGraph } from "../viewerTypes";

export type MarkdownCodeMirrorEditorProps = {
  value: string;
  readOnly: boolean;
  livePreview: boolean;
  aiEditFile?: AiEditFile | null;
  htmlTrustMode?: MarkdownHtmlTrustMode;
  documentPath?: string;
  markdownLinkGraph?: MarkdownLinkGraph | null;
  onChange?: (value: string) => void;
};

const externalDocumentUpdate = Annotation.define<boolean>();

export function MarkdownCodeMirrorEditor({
  value,
  readOnly,
  livePreview,
  aiEditFile = null,
  htmlTrustMode = "safe",
  documentPath = "",
  markdownLinkGraph = null,
  onChange,
}: MarkdownCodeMirrorEditorProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  const editableCompartmentRef = useRef(new Compartment());
  const livePreviewCompartmentRef = useRef(new Compartment());
  const aiEditCompartmentRef = useRef(new Compartment());

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;

    const view = new EditorView({
      parent: host,
      state: EditorState.create({
        doc: value,
        extensions: [
          ...markdownCodeMirrorBaseExtensions(readOnly),
          editableCompartmentRef.current.of(getEditableExtensions(readOnly)),
          livePreviewCompartmentRef.current.of(livePreview ? markdownLivePreviewExtension(htmlTrustMode, markdownLinkGraph, documentPath) : []),
          aiEditCompartmentRef.current.of(markdownAiEditExtension(aiEditFile)),
          EditorView.updateListener.of((update) => {
            if (!update.docChanged) return;
            if (update.transactions.some((transaction) => transaction.annotation(externalDocumentUpdate))) return;
            onChangeRef.current?.(update.state.doc.toString());
          }),
        ],
      }),
    });

    viewRef.current = view;

    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, []);

  useLayoutEffect(() => {
    const view = viewRef.current;
    if (!view) return;

    view.dispatch({
      effects: editableCompartmentRef.current.reconfigure(getEditableExtensions(readOnly)),
    });
  }, [readOnly]);

  useLayoutEffect(() => {
    const view = viewRef.current;
    if (!view) return;

    view.dispatch({
      effects: livePreviewCompartmentRef.current.reconfigure(livePreview ? markdownLivePreviewExtension(htmlTrustMode, markdownLinkGraph, documentPath) : []),
    });
  }, [documentPath, livePreview, htmlTrustMode, markdownLinkGraph]);

  useLayoutEffect(() => {
    const view = viewRef.current;
    if (!view) return;

    try {
      view.dispatch({
        effects: aiEditCompartmentRef.current.reconfigure(markdownAiEditExtension(aiEditFile)),
      });
    } catch (error) {
      console.warn("Unable to apply AI edit decorations:", error);
      view.dispatch({
        effects: aiEditCompartmentRef.current.reconfigure([]),
      });
    }
  }, [aiEditFile]);

  useLayoutEffect(() => {
    const view = viewRef.current;
    if (!view) return;

    const currentValue = view.state.doc.toString();
    if (currentValue === value) return;

    view.dispatch({
      changes: { from: 0, to: currentValue.length, insert: value },
      annotations: externalDocumentUpdate.of(true),
    });
  }, [value]);

  return (
    <div
      ref={hostRef}
      className="markdown-codemirror-editor"
      data-live-preview={livePreview ? "true" : "false"}
      data-readonly={readOnly ? "true" : "false"}
    />
  );
}

function getEditableExtensions(readOnly: boolean): Extension[] {
  return [
    EditorState.readOnly.of(readOnly),
    EditorView.editable.of(!readOnly),
  ];
}
