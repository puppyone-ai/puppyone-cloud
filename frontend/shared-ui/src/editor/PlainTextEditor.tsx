"use client";

import { ConflictMarkerBanner } from "./ConflictMarkerBanner";

const technicalTextExtensions = [".env", ".log", ".json", ".yml", ".yaml", ".toml", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".md"];

export type PlainTextEditorProps = {
  content: string;
  nodeName?: string;
  readOnly?: boolean;
  onChange?: (content: string) => void;
};

export function PlainTextEditor({
  content,
  nodeName = "",
  readOnly = true,
  onChange,
}: PlainTextEditorProps) {
  const lowerName = nodeName.toLowerCase();
  const isTechnicalText = technicalTextExtensions.some((extension) => lowerName.endsWith(extension));

  return (
    <div className="plain-text-editor">
      <ConflictMarkerBanner content={content} onResolve={readOnly ? undefined : onChange} />
      <div className="plain-text-editor__rail">
        {readOnly ? (
          <pre
            className="plain-text-editor__surface"
            data-technical={isTechnicalText}
            aria-label={nodeName ? `Read ${nodeName}` : "Read text file"}
          >
            {content}
          </pre>
        ) : (
          <textarea
            className="plain-text-editor__surface plain-text-editor__input"
            data-technical={isTechnicalText}
            value={content}
            onChange={(event) => onChange?.(event.currentTarget.value)}
            placeholder="Start writing..."
            aria-label={nodeName ? `Edit ${nodeName}` : "Edit text file"}
            spellCheck={false}
          />
        )}
      </div>
    </div>
  );
}

export default PlainTextEditor;
