import type { DataNode, FileContent } from "../core/types";
import type { FileIconThemeId } from "../file/fileIcons";
import { PuppyoneEditorHost, type EditorSaveMode } from "./PuppyoneEditorHost";
import type { AiEditFile } from "./ai-edits/types";
import type { MarkdownHtmlTrustMode, MarkdownLinkGraph } from "./viewerTypes";

export type EditorHostProps = {
  node: DataNode;
  fileContent?: FileContent | null;
  fileUrl?: string | null;
  fileUrlLoading?: boolean;
  fileUrlError?: string | null;
  loading?: boolean;
  error?: string | null;
  onSaveContent?: (content: string) => Promise<void>;
  aiEditFile?: AiEditFile | null;
  hideSourceView?: boolean;
  fileIconTheme?: FileIconThemeId;
  saveMode?: EditorSaveMode;
  htmlTrustMode?: MarkdownHtmlTrustMode;
  markdownLinkGraph?: MarkdownLinkGraph | null;
  deferFallbackContent?: boolean;
};

export function EditorHost({
  node,
  fileContent,
  fileUrl = null,
  fileUrlLoading = false,
  fileUrlError = null,
  loading = false,
  error = null,
  onSaveContent,
  aiEditFile = null,
  hideSourceView = false,
  fileIconTheme = "default",
  saveMode = "manual",
  htmlTrustMode = "safe",
  markdownLinkGraph = null,
  deferFallbackContent = false,
}: EditorHostProps) {
  return (
    <PuppyoneEditorHost
      document={{
        path: node.path,
        name: node.name,
        type: fileContent?.type ?? node.type,
        content: fileContent?.content ?? (deferFallbackContent ? undefined : node.content),
        preview: deferFallbackContent ? undefined : node.preview,
        mimeType: fileContent?.mimeType,
        url: fileContent?.url ?? fileUrl,
      }}
      loading={loading}
      error={error}
      fileUrlLoading={fileUrlLoading}
      fileUrlError={fileUrlError}
      onSaveContent={onSaveContent}
      aiEditFile={aiEditFile}
      hideSourceView={hideSourceView}
      fileIconTheme={fileIconTheme}
      saveMode={saveMode}
      htmlTrustMode={htmlTrustMode}
      markdownLinkGraph={markdownLinkGraph}
    />
  );
}
