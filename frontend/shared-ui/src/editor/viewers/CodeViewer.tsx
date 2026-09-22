import { TextEditorFrame } from "./TextEditorFrame";
import { DocumentPreview } from "./DocumentFallbackViewer";
import { getDocumentLabel, isTextEditable } from "./viewerUtils";
import type { EditorViewerContext } from "../viewerTypes";

export function JsonViewer(context: EditorViewerContext) {
  return (
    <TextEditorFrame
      documentId={context.document.path}
      content={context.content}
      nodeName={context.document.name}
      defaultMode="source"
      canEdit={context.canEdit}
      onSaveContent={context.onSaveContent}
      hideSourceView={context.hideSourceView}
      saveMode={context.saveMode}
      renderLive={(value) => <CodePreview language="JSON" content={value} />}
    />
  );
}

export function TextFileViewer(context: EditorViewerContext) {
  if (!context.content) {
    return <DocumentPreview document={context.document} title="No text preview available." />;
  }

  return (
    <TextEditorFrame
      documentId={context.document.path}
      content={context.content}
      nodeName={context.document.name}
      defaultMode="source"
      canEdit={context.canEdit}
      onSaveContent={context.onSaveContent}
      hideSourceView={context.hideSourceView}
      saveMode={context.saveMode}
      renderLive={(value) => <CodePreview language={getDocumentLabel(context.document)} content={value} />}
    />
  );
}

export function canEditTextFile(context: Pick<EditorViewerContext, "document" | "content">): boolean {
  return isTextEditable(context.document, context.content);
}

function CodePreview({ language, content }: { language: string; content: string }) {
  return (
    <div className="code-preview">
      <div className="code-preview-toolbar">
        <span>{language}</span>
      </div>
      <pre>
        {content.split("\n").map((line, index) => (
          <span key={index} className="code-line">
            <span className="line-number">{index + 1}</span>
            <span>{line || " "}</span>
          </span>
        ))}
      </pre>
    </div>
  );
}
