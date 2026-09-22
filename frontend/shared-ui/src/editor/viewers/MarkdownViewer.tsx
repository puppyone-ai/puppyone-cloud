"use client";

import { MarkdownCodeMirrorEditor } from "../markdown/MarkdownCodeMirrorEditor";
import type { EditorViewerContext } from "../viewerTypes";
import { TextEditorFrame } from "./TextEditorFrame";

export function MarkdownViewer(context: EditorViewerContext) {
  return (
    <TextEditorFrame
      documentId={context.document.path}
      content={context.content}
      nodeName={context.document.name}
      defaultMode="live"
      canEdit={context.canEdit}
      onSaveContent={context.onSaveContent}
      hideSourceView={context.hideSourceView}
      saveMode={context.saveMode}
      renderLive={(value, controls) => (
        <MarkdownCodeMirrorEditor
          value={value}
          readOnly={!controls.canEdit}
          livePreview
          aiEditFile={context.aiEditFile}
          htmlTrustMode={context.htmlTrustMode}
          documentPath={context.document.path}
          markdownLinkGraph={context.markdownLinkGraph}
          onChange={controls.canEdit ? controls.onChange : undefined}
        />
      )}
      renderSource={(value, controls) => (
        <MarkdownCodeMirrorEditor
          value={value}
          readOnly={!controls.canEdit}
          livePreview={false}
          aiEditFile={context.aiEditFile}
          htmlTrustMode={context.htmlTrustMode}
          documentPath={context.document.path}
          markdownLinkGraph={context.markdownLinkGraph}
          onChange={controls.canEdit ? controls.onChange : undefined}
        />
      )}
    />
  );
}

export function canEditMarkdown() {
  return true;
}
