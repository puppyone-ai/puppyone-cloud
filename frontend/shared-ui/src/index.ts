export * from "./core/types";
export type {
  FileCategory,
  FileFormat,
  FilePreviewKind,
  FileSemanticKind,
  GenericViewerId,
  IngestStrategy,
  SpecialViewerId,
  ViewerId,
} from "./core/fileFormats";
export {
  FILE_FORMATS,
  UNKNOWN_FORMAT,
  getFilePreviewKind,
  getFileSemanticKind,
  getMatchedExtension,
  getPreferredMimeType,
  getPreviewKindForFormat,
  getSemanticKindForFormat,
  isKnownFileFormat,
  isTextLikeFile,
  isTextLikeFileFormat,
  resolveFileFormat,
} from "./core/fileFormats";

export { DataWorkspace } from "./data/DataWorkspace";
export type { DataWorkspaceProps, DataWorkspaceState } from "./data/DataWorkspace";
export { EXPLORER_TREE_NODE_DRAG_TYPE, ExplorerTree } from "./data/ExplorerTree";
export type { ExplorerTreeProps } from "./data/ExplorerTree";
export { FilePreview } from "./data/FilePreview";
export type { FilePreviewProps } from "./data/FilePreview";
export { ProjectsHeader as WorkspaceHeader } from "./data/ProjectsHeader";
export type {
  BreadcrumbSegment,
  ProjectsHeaderProps as WorkspaceHeaderProps,
} from "./data/ProjectsHeader";

export { EditorHost } from "./editor/EditorHost";
export type { EditorHostProps } from "./editor/EditorHost";
export {
  EDITOR_VIEWERS,
  getEditorSourceRequirement,
  resolveEditorViewer,
  shouldReadEditorContent,
} from "./editor/viewerRegistry";
export type {
  EditorDocument,
  EditorDocumentKind,
  EditorMode,
  EditorSaveMode,
  EditorSourceRequirement,
  EditorViewer,
  EditorViewerContext,
  EditorViewerMatch,
  MarkdownBacklink,
  MarkdownBacklinkReference,
  MarkdownHtmlTrustMode,
  MarkdownLinkGraph,
  MarkdownWikiLinkResolvedTarget,
} from "./editor/viewerTypes";
export { PlainTextEditor } from "./editor/PlainTextEditor";
export type { PlainTextEditorProps } from "./editor/PlainTextEditor";
export { CsvTableEditor } from "./editor/CsvTableEditor";
export type { CsvTableEditorProps } from "./editor/CsvTableEditor";
export { EditorSaveButton as SaveStatusButton } from "./editor/EditorSaveButton";
export type {
  EditorSaveButtonProps as SaveStatusButtonProps,
  SaveStatus,
} from "./editor/EditorSaveButton";
export { ConflictMarkerBanner } from "./editor/ConflictMarkerBanner";
export {
  createAiEditFile,
  createAiEditRequest,
  getAiEditFileForPath,
  getAiEditTotals,
} from "./editor/ai-edits/diff";
export type {
  AiEditFile,
  AiEditFileStatus,
  AiEditHunk,
  AiEditHunkKind,
  AiEditHunkState,
  AiEditLineRange,
  AiEditRequest,
} from "./editor/ai-edits/types";

export {
  FILE_ICON_THEMES,
  FILE_TYPE_ICONS,
  FileGlyphIcon,
  FilePreviewIcon,
  getFileAccent,
  getFileExtension,
  getFileIcon,
  getFileVisualKind,
  isFileIconThemeId,
} from "./file/fileIcons";
export type { FileIconThemeId, FileVisualKind } from "./file/fileIcons";

export { Button } from "./primitives/Button";
export type { ButtonProps } from "./primitives/Button";
export { IconButton } from "./primitives/IconButton";
export type { IconButtonProps } from "./primitives/IconButton";
export { EmptyState } from "./primitives/EmptyState";
export type { EmptyStateProps } from "./primitives/EmptyState";
export { StatusBadge } from "./primitives/StatusBadge";
export type { StatusBadgeProps } from "./primitives/StatusBadge";
