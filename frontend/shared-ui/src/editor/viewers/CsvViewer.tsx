import { CsvTableEditor } from "../CsvTableEditor";
import type { EditorViewerContext } from "../viewerTypes";
import { TextEditorFrame } from "./TextEditorFrame";
import { getDelimitedTableDelimiter, isTextEditable } from "./viewerUtils";

export function CsvViewer(context: EditorViewerContext) {
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
        <CsvTableEditor
          documentId={context.document.path}
          content={value}
          nodeName={context.document.name}
          delimiter={getDelimitedTableDelimiter(context.document)}
          readOnly={!controls.canEdit}
          onChange={controls.canEdit ? controls.onChange : undefined}
        />
      )}
    />
  );
}

export function canEditCsv(context: Pick<EditorViewerContext, "document" | "content">): boolean {
  return isTextEditable(context.document, context.content);
}
