import type { ReactNode } from "react";
import {
  File as LucideFile,
  FileArchive,
  FileAudio,
  FileCode,
  FileImage,
  FileJson,
  FileSpreadsheet,
  FileText,
  FileVideo,
  Folder as LucideFolder,
  type LucideIcon,
} from "lucide-react";
import { getFileAccent, type FileIconThemeId, type FileIconThemeMetadata, type FileVisualKind } from "./fileIconTypes";

export type FileIconRenderContext = {
  kind: FileVisualKind;
  name: string;
  type?: string | null;
  label: string;
  size: number;
  color: string;
};

export type FileIconPreviewContext = FileIconRenderContext & {
  snippet?: string | null;
  childrenCount?: number | null;
};

type FileIconRenderer<TContext extends FileIconRenderContext> = (context: TContext) => ReactNode;

export type FileIconThemeDefinition = FileIconThemeMetadata & {
  renderGlyph: FileIconRenderer<FileIconRenderContext>;
  renderPreview: FileIconRenderer<FileIconPreviewContext>;
};

const FILE_ICON_THEME_ORDER: readonly FileIconThemeId[] = ["default", "lines", "vscode", "material", "minimal"];
const SNIPPET_PREVIEW_KINDS = new Set<FileVisualKind>(["markdown", "json"]);

const defaultTheme: FileIconThemeDefinition = {
  id: "default",
  label: "Default",
  description: "PuppyOne classic file icons.",
  renderGlyph: renderDefaultGlyph,
  renderPreview: renderDefaultPreview,
};

const linesTheme: FileIconThemeDefinition = createThemeVariant({
  id: "lines",
  label: "Lines",
  description: "Standalone line icon for Markdown documents.",
  base: defaultTheme,
  glyphOverrides: {
    markdown: renderStandaloneLinesGlyph,
  },
  previewOverrides: {
    markdown: renderStandaloneLinesPreview,
  },
});

const vscodeTheme: FileIconThemeDefinition = createIconTheme({
  id: "vscode",
  label: "VS Code",
  description: "VS Code-style semantic file icons.",
  renderGlyph: renderVsCodeGlyph,
});

const materialTheme: FileIconThemeDefinition = createIconTheme({
  id: "material",
  label: "Material",
  description: "Filled, colorful document icons.",
  renderGlyph: renderMaterialGlyph,
});

const minimalTheme: FileIconThemeDefinition = createIconTheme({
  id: "minimal",
  label: "Minimal",
  description: "Thin outline icons.",
  renderGlyph: renderMinimalGlyph,
});

export const FILE_ICON_THEME_REGISTRY = {
  default: defaultTheme,
  lines: linesTheme,
  vscode: vscodeTheme,
  material: materialTheme,
  minimal: minimalTheme,
} satisfies Record<FileIconThemeId, FileIconThemeDefinition>;

export const FILE_ICON_THEMES = FILE_ICON_THEME_ORDER.map((id) => getThemeMetadata(FILE_ICON_THEME_REGISTRY[id])) as readonly FileIconThemeMetadata[];

const FILE_ICON_THEME_IDS = new Set<string>(FILE_ICON_THEME_ORDER);

export function isFileIconThemeId(value: string | null | undefined): value is FileIconThemeId {
  return typeof value === "string" && FILE_ICON_THEME_IDS.has(value);
}

export function getFileIconThemeDefinition(theme?: FileIconThemeId | null): FileIconThemeDefinition {
  return isFileIconThemeId(theme) ? FILE_ICON_THEME_REGISTRY[theme] : FILE_ICON_THEME_REGISTRY.default;
}

function getThemeMetadata(theme: FileIconThemeDefinition): FileIconThemeMetadata {
  return {
    id: theme.id,
    label: theme.label,
    description: theme.description,
  };
}

function createThemeVariant({
  id,
  label,
  description,
  base,
  glyphOverrides = {},
  previewOverrides = {},
}: FileIconThemeMetadata & {
  base: FileIconThemeDefinition;
  glyphOverrides?: Partial<Record<FileVisualKind, FileIconRenderer<FileIconRenderContext>>>;
  previewOverrides?: Partial<Record<FileVisualKind, FileIconRenderer<FileIconPreviewContext>>>;
}): FileIconThemeDefinition {
  return {
    id,
    label,
    description,
    renderGlyph: (context) => (glyphOverrides[context.kind] ?? base.renderGlyph)(context),
    renderPreview: (context) => (previewOverrides[context.kind] ?? base.renderPreview)(context),
  };
}

function createIconTheme({
  id,
  label,
  description,
  renderGlyph,
}: FileIconThemeMetadata & {
  renderGlyph: FileIconRenderer<FileIconRenderContext>;
}): FileIconThemeDefinition {
  return {
    id,
    label,
    description,
    renderGlyph,
    renderPreview: (context) => renderIconThemePreview(context, id, renderGlyph),
  };
}

function renderDefaultPreview(context: FileIconPreviewContext): ReactNode {
  if (context.kind === "folder") return renderFolderPreview(context, "default");

  if (SNIPPET_PREVIEW_KINDS.has(context.kind) && context.snippet) {
    return (
      <DocShell size={context.size}>
        <div
          style={{
            height: "100%",
            overflow: "hidden",
            color: context.kind === "json" ? "var(--po-file-accent-json)" : "var(--po-text-muted)",
            fontFamily: "var(--po-font-sans)",
            fontSize: Math.max(4, context.size * 0.078),
            lineHeight: 1.45,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {context.snippet}
        </div>
      </DocShell>
    );
  }

  return renderDocShellPreview(context, renderDefaultPreviewGlyph(context));
}

function renderIconThemePreview(
  context: FileIconPreviewContext,
  theme: FileIconThemeId,
  renderGlyph: FileIconRenderer<FileIconRenderContext>,
): ReactNode {
  if (context.kind === "folder") return renderFolderPreview(context, theme);

  if (SNIPPET_PREVIEW_KINDS.has(context.kind) && context.snippet) {
    return renderCenteredIcon(context.size, renderGlyph({ ...context, size: Math.round(context.size * 0.78) }));
  }

  return renderDocShellPreview(
    context,
    renderCenteredIcon("100%", renderGlyph({ ...context, size: Math.max(18, Math.round(context.size * 0.5)) })),
  );
}

function renderFolderPreview(context: FileIconPreviewContext, theme: FileIconThemeId): ReactNode {
  return (
    <div
      style={{
        position: "relative",
        width: context.size,
        height: context.size,
        display: "grid",
        placeItems: "center",
      }}
    >
      <FolderGlyph size={context.size} theme={theme} />
      {context.childrenCount != null && context.childrenCount > 0 && (
        <span
          style={{
            position: "absolute",
            right: -4,
            bottom: -1,
            minWidth: 18,
            height: 18,
            padding: "0 5px",
            borderRadius: 999,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            background: "var(--po-panel-raised)",
            border: "1px solid var(--po-border)",
            color: "var(--po-text-muted)",
            fontSize: 10,
            fontWeight: 700,
            lineHeight: 1,
          }}
        >
          {context.childrenCount}
        </span>
      )}
    </div>
  );
}

function renderDocShellPreview(context: FileIconRenderContext, children: ReactNode): ReactNode {
  return (
    <DocShell size={context.size}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: "100%",
          height: "100%",
          minWidth: 0,
        }}
      >
        {children}
      </div>
    </DocShell>
  );
}

function renderStandaloneLinesPreview(context: FileIconPreviewContext): ReactNode {
  return renderCenteredIcon(context.size, <StandaloneDocumentLinesGlyph size={Math.round(context.size * 0.62)} color={context.color} />);
}

function renderCenteredIcon(size: number | string, children: ReactNode): ReactNode {
  return (
    <div
      style={{
        width: size,
        height: size,
        display: "grid",
        placeItems: "center",
      }}
    >
      {children}
    </div>
  );
}

function DocShell({
  size,
  children,
}: Readonly<{
  size: number;
  children?: ReactNode;
}>) {
  const width = Math.round(size * 0.74);
  const height = Math.round(size * 0.9);
  const scale = width / 44;

  return (
    <div style={{ width: size, height: size, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div
        style={{
          position: "relative",
          width,
          height,
          filter: "drop-shadow(0 1px 1.5px var(--po-file-icon-shadow))",
        }}
      >
        <svg width={width} height={height} viewBox="0 0 44 54" fill="none" style={{ position: "absolute", inset: 0 }} aria-hidden>
          <path
            d="M5.5 2.5H28.5L39.5 13.5V51.5H5.5V2.5Z"
            fill="var(--po-file-icon-body)"
            stroke="var(--po-file-icon-stroke)"
            strokeWidth="1.35"
            strokeLinejoin="round"
          />
          <path d="M28.5 2.5V13.5H39.5" stroke="var(--po-file-icon-stroke)" strokeWidth="1.35" strokeLinejoin="round" />
          <path d="M28.5 2.5V13.5H39.5L28.5 2.5Z" fill="var(--po-file-icon-fold)" />
        </svg>
        <div
          style={{
            position: "absolute",
            top: 16 * scale,
            left: 8 * scale,
            right: 7 * scale,
            bottom: 6 * scale,
            overflow: "hidden",
          }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

const DEFAULT_GLYPH_RENDERERS: Partial<Record<FileVisualKind, FileIconRenderer<FileIconRenderContext>>> = {
  audio: renderDefaultAudioGlyph,
  image: renderDefaultImageGlyph,
  html: renderDefaultCodeGlyph,
  code: renderDefaultCodeGlyph,
  json: renderDefaultJsonGlyph,
};

function renderDefaultGlyph(context: FileIconRenderContext): ReactNode {
  if (context.kind === "folder") return <FolderGlyph size={context.size} compact theme="default" />;
  return (DEFAULT_GLYPH_RENDERERS[context.kind] ?? renderDefaultDocumentGlyph)(context);
}

function renderDefaultAudioGlyph({ color, size }: FileIconRenderContext): ReactNode {
  return (
    <svg width={size} height={size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <path d="M2.6 10.9V7.1h2.25L8.7 4.35v9.3L4.85 10.9H2.6Z" fill={color} />
      <path d="M10.8 6.55c1.05 1.1 1.05 2.8 0 3.9" stroke={color} strokeWidth="1.45" strokeLinecap="round" />
      <path d="M12.95 5.05c1.8 1.95 1.8 5.9 0 7.9" stroke={color} strokeWidth="1.25" strokeLinecap="round" opacity="0.78" />
    </svg>
  );
}

function renderDefaultImageGlyph({ color, size }: FileIconRenderContext): ReactNode {
  return (
    <svg width={size} height={size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <rect x="2.75" y="3.75" width="12.5" height="10.5" rx="1.25" stroke={color} strokeWidth="1.45" />
      <path d="M3.8 12.5 6.35 9.65l2.05 2.1 2.35-3.05 3.35 3.8" stroke={color} strokeWidth="1.45" strokeLinecap="round" strokeLinejoin="round" />
      <rect x="10.85" y="5.6" width="2" height="2" rx="0.35" fill={color} />
    </svg>
  );
}

function renderDefaultCodeGlyph({ color, kind, size }: FileIconRenderContext): ReactNode {
  return (
    <svg width={size} height={size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <path d="m7.05 5.15-3.5 3.75 3.5 3.75" stroke={color} strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" />
      <path d="m10.95 5.15 3.5 3.75-3.5 3.75" stroke={color} strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" />
      {kind === "html" && <path d="M9.95 4.95 8.05 12.9" stroke={color} strokeWidth="1.35" strokeLinecap="round" opacity="0.86" />}
    </svg>
  );
}

function renderDefaultJsonGlyph({ color, size }: FileIconRenderContext): ReactNode {
  return (
    <svg width={size} height={size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <text x="9" y="12.35" textAnchor="middle" fontSize="9.5" fontWeight="800" fontFamily="var(--po-font-sans)" fill={color}>
        {"{}"}
      </text>
    </svg>
  );
}

function renderDefaultDocumentGlyph({ color, size }: FileIconRenderContext): ReactNode {
  return (
    <svg width={size} height={size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <path
        d="M5.1 2.75h5.65l2.6 2.65v8.5c0 .5-.4.9-.9.9h-7.35c-.5 0-.9-.4-.9-.9V3.65c0-.5.4-.9.9-.9Z"
        fill="color-mix(in srgb, var(--po-file-icon-body) 65%, transparent)"
        stroke={color}
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <path d="M10.75 2.95v2.45h2.4" stroke={color} strokeWidth="1" strokeLinejoin="round" />
      <path d="M5.85 8.25h5.2M5.85 10.25h5.2M5.85 12.25h3.65" stroke={color} strokeWidth="1.05" strokeLinecap="round" opacity="0.9" />
    </svg>
  );
}

const DEFAULT_PREVIEW_GLYPH_RENDERERS: Partial<Record<FileVisualKind, FileIconRenderer<FileIconRenderContext>>> = {
  image: renderDefaultImagePreviewGlyph,
  audio: renderDefaultAudioPreviewGlyph,
  video: renderDefaultVideoPreviewGlyph,
  html: renderDefaultCodePreviewGlyph,
  code: renderDefaultCodePreviewGlyph,
  spreadsheet: renderDefaultSpreadsheetPreviewGlyph,
  archive: renderDefaultArchivePreviewGlyph,
};

function renderDefaultPreviewGlyph(context: FileIconRenderContext): ReactNode {
  return (DEFAULT_PREVIEW_GLYPH_RENDERERS[context.kind] ?? renderDefaultDocumentPreviewGlyph)(context);
}

function renderDefaultImagePreviewGlyph({ color }: FileIconRenderContext): ReactNode {
  return (
    <svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" aria-hidden>
      <rect x="5.5" y="7" width="21" height="17.5" rx="2.4" stroke={color} strokeWidth="2" />
      <path d="M7.5 22.5 13 16.9l4.2 4.1 3.7-5 4.1 6.5" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="21.7" cy="11.8" r="1.85" fill={color} />
    </svg>
  );
}

function renderDefaultAudioPreviewGlyph({ color }: FileIconRenderContext): ReactNode {
  return (
    <svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" aria-hidden>
      <path d="M6.5 19.5v-7h4.4l7.1-4.7v16.4l-7.1-4.7H6.5Z" fill={color} />
      <path d="M21.1 11.4c2.1 2.35 2.1 6.85 0 9.2" stroke={color} strokeWidth="2.1" strokeLinecap="round" />
      <path d="M24.8 8.7c3.5 3.95 3.5 10.65 0 14.6" stroke={color} strokeWidth="1.8" strokeLinecap="round" opacity="0.72" />
    </svg>
  );
}

function renderDefaultVideoPreviewGlyph({ color }: FileIconRenderContext): ReactNode {
  return (
    <svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" aria-hidden>
      <rect x="5.5" y="8" width="21" height="16" rx="2.4" stroke={color} strokeWidth="2" />
      <path d="m14 12.4 7 3.6-7 3.6v-7.2Z" fill={color} />
    </svg>
  );
}

function renderDefaultCodePreviewGlyph({ color, kind }: FileIconRenderContext): ReactNode {
  return (
    <svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" aria-hidden>
      <path d="m13.2 10.2-5.1 5.9 5.1 5.8" stroke={color} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="m18.8 10.2 5.1 5.9-5.1 5.8" stroke={color} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      {kind === "html" && <path d="m17.9 9.7-3.8 12.6" stroke={color} strokeWidth="1.85" strokeLinecap="round" opacity="0.78" />}
    </svg>
  );
}

function renderDefaultSpreadsheetPreviewGlyph({ color }: FileIconRenderContext): ReactNode {
  return (
    <svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" aria-hidden>
      <rect x="6.5" y="7" width="19" height="18" rx="2" stroke={color} strokeWidth="2" />
      <path d="M6.5 13h19M6.5 19h19M13 7v18M19.5 7v18" stroke={color} strokeWidth="1.4" opacity="0.84" />
    </svg>
  );
}

function renderDefaultArchivePreviewGlyph({ color }: FileIconRenderContext): ReactNode {
  return (
    <svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" aria-hidden>
      <path d="M7.5 11 16 6.5l8.5 4.5v10L16 25.5 7.5 21V11Z" stroke={color} strokeWidth="2" strokeLinejoin="round" />
      <path d="M7.8 11.2 16 15.6l8.2-4.4M16 15.6v9.4" stroke={color} strokeWidth="1.6" strokeLinejoin="round" opacity="0.8" />
    </svg>
  );
}

function renderDefaultDocumentPreviewGlyph({ color, size }: FileIconRenderContext): ReactNode {
  return (
    <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: Math.max(1.4, Math.min(3, size * 0.055)) }}>
      {[82, 92, 62, 72].map((width, index) => (
        <span
          key={`${width}-${index}`}
          style={{
            width: `${width}%`,
            height: Math.max(1, Math.min(2, size * 0.035)),
            borderRadius: 999,
            background: color,
            opacity: 0.64 - index * 0.08,
          }}
        />
      ))}
    </div>
  );
}

function renderVsCodeGlyph(context: FileIconRenderContext): ReactNode {
  const color = getVsCodeAccent(context.kind);
  const fill = getVsCodeFill(context.kind);
  const foldFill = getVsCodeFoldFill(context.kind);

  if (context.kind === "folder") return <VsCodeFolderGlyph size={context.size} />;

  return (
    <svg width={context.size} height={context.size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <path
        d="M4.35 2.25h6.35l3.05 3.05v9.3c0 .62-.5 1.12-1.12 1.12H4.35c-.62 0-1.12-.5-1.12-1.12V3.37c0-.62.5-1.12 1.12-1.12Z"
        fill={fill}
      />
      <path d="M10.7 2.25V5.3h3.05" fill={foldFill} />
      <VsCodeSymbol kind={context.kind} color={color} label={context.label} />
    </svg>
  );
}

function VsCodeFolderGlyph({ size }: { size: number }) {
  const tabFill = "color-mix(in srgb, #dcb67a 70%, var(--po-file-icon-body))";
  const bodyFill = "color-mix(in srgb, #c99646 76%, var(--po-file-icon-body))";

  return (
    <svg width={size} height={size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <path
        d="M1.85 5.2c0-.72.58-1.3 1.3-1.3h4.2l1.35 1.45h6.15c.72 0 1.3.58 1.3 1.3v6.25c0 .72-.58 1.3-1.3 1.3H3.15c-.72 0-1.3-.58-1.3-1.3V5.2Z"
        fill={tabFill}
      />
      <path d="M2.05 7h13.9v5.95c0 .7-.57 1.25-1.25 1.25H3.3c-.68 0-1.25-.55-1.25-1.25V7Z" fill={bodyFill} />
    </svg>
  );
}

function VsCodeSymbol({
  kind,
  color,
  label,
}: {
  kind: FileVisualKind;
  color: string;
  label?: string;
}) {
  const renderer = VSCODE_SYMBOL_RENDERERS[kind] ?? renderVsCodeLabelSymbol;
  return renderer({ kind, color, label });
}

type SymbolContext = {
  kind: FileVisualKind;
  color: string;
  label?: string;
};

const VSCODE_SYMBOL_RENDERERS: Partial<Record<FileVisualKind, (context: SymbolContext) => ReactNode>> = {
  markdown: ({ color }) => <DocumentLinesSymbol color={color} />,
  json: ({ color }) => (
    <text x="8.75" y="12.15" textAnchor="middle" fontSize="8.2" fontWeight="850" fontFamily="var(--po-font-sans)" fill={color}>
      {"{}"}
    </text>
  ),
  html: renderVsCodeCodeSymbol,
  code: renderVsCodeCodeSymbol,
  image: ({ color }) => (
    <>
      <rect x="5" y="6.15" width="8" height="6.3" rx="0.75" stroke={color} strokeWidth="1" />
      <path d="m5.55 11.85 1.65-1.75 1.35 1.15 1.6-2.05 2.3 2.65" stroke={color} strokeWidth="1" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="11.45" cy="7.65" r="0.58" fill={color} />
    </>
  ),
  audio: ({ color }) => (
    <>
      <path d="M5.15 10.55v-3.1h1.75l2.4-1.75v6.6l-2.4-1.75H5.15Z" fill={color} />
      <path d="M11.25 7.1c.85.9.85 2.9 0 3.8" stroke={color} strokeWidth="1.05" strokeLinecap="round" />
    </>
  ),
  video: ({ color }) => (
    <>
      <rect x="4.95" y="5.95" width="8.1" height="6.6" rx="0.85" stroke={color} strokeWidth="1" />
      <path d="m8.1 7.55 3.05 1.7-3.05 1.7v-3.4Z" fill={color} />
    </>
  ),
  spreadsheet: ({ color }) => (
    <>
      <rect x="5" y="5.7" width="8" height="7.2" rx="0.6" stroke={color} strokeWidth="1" />
      <path d="M5.1 8.1h7.8M5.1 10.55h7.8M7.7 5.8v7M10.3 5.8v7" stroke={color} strokeWidth="0.65" opacity="0.86" />
    </>
  ),
  archive: ({ color }) => (
    <>
      <path d="M5.15 7.15 9 5.1l3.85 2.05v4.15L9 13.35 5.15 11.3V7.15Z" stroke={color} strokeWidth="1" strokeLinejoin="round" />
      <path d="M5.35 7.25 9 9.2l3.65-1.95M9 9.2v3.8" stroke={color} strokeWidth="0.8" opacity="0.86" />
    </>
  ),
};

function renderVsCodeCodeSymbol({ color, kind }: SymbolContext): ReactNode {
  return (
    <>
      <path d="m7.1 6.05-2.6 2.85 2.6 2.85" stroke={color} strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" />
      <path d="m10.9 6.05 2.6 2.85-2.6 2.85" stroke={color} strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" />
      {kind === "html" && <path d="M9.95 5.75 8.05 12.25" stroke={color} strokeWidth="1" strokeLinecap="round" opacity="0.82" />}
    </>
  );
}

function renderVsCodeLabelSymbol({ color, kind, label }: SymbolContext): ReactNode {
  return (
    <text
      x="8.75"
      y="11.55"
      textAnchor="middle"
      fontSize={kind === "pdf" ? "4.2" : "5.5"}
      fontWeight="850"
      fontFamily="var(--po-font-sans)"
      fill={color}
    >
      {kind === "pdf" ? "PDF" : label ?? getVsCodeLabel(kind)}
    </text>
  );
}

function getVsCodeAccent(kind: FileVisualKind): string {
  const overrides: Partial<Record<FileVisualKind, string>> = {
    archive: "var(--po-warning)",
    document: "var(--po-info)",
    binary: "var(--po-file-accent-sheet)",
  };

  return overrides[kind] ?? getFileAccent(kind);
}

function getVsCodeFill(kind: FileVisualKind): string {
  const accent = getVsCodeAccent(kind);
  return `color-mix(in srgb, ${accent} 16%, var(--po-file-icon-body))`;
}

function getVsCodeFoldFill(kind: FileVisualKind): string {
  const accent = getVsCodeAccent(kind);
  return `color-mix(in srgb, ${accent} 13%, var(--po-file-icon-fold))`;
}

function getVsCodeLabel(kind: FileVisualKind): string {
  const labels: Partial<Record<FileVisualKind, string>> = {
    document: "DOC",
    binary: "BIN",
    text: "TXT",
  };

  return labels[kind] ?? "FILE";
}

const MATERIAL_GLYPH_RENDERERS: Partial<Record<FileVisualKind, FileIconRenderer<FileIconRenderContext>>> = {
  audio: renderMaterialAudioGlyph,
  image: renderMaterialImageGlyph,
  video: renderMaterialVideoGlyph,
  html: renderMaterialCodeGlyph,
  code: renderMaterialCodeGlyph,
  spreadsheet: renderMaterialSpreadsheetGlyph,
  archive: renderMaterialArchiveGlyph,
};

function renderMaterialGlyph(context: FileIconRenderContext): ReactNode {
  if (context.kind === "folder") return <FolderGlyph size={context.size} compact theme="material" />;
  return (MATERIAL_GLYPH_RENDERERS[context.kind] ?? renderMaterialDocumentGlyph)(context);
}

function renderMaterialAudioGlyph({ color, size }: FileIconRenderContext): ReactNode {
  return (
    <svg width={size} height={size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <path d="M2.7 10.75v-3.5h2.15l3.65-2.5v8.5l-3.65-2.5H2.7Z" fill={color} />
      <path d="M10.6 6.45c1.2 1.1 1.2 4 0 5.1" stroke={color} strokeWidth="1.55" strokeLinecap="round" />
      <path d="M12.8 5.2c1.85 2 1.85 5.6 0 7.6" stroke={color} strokeWidth="1.25" strokeLinecap="round" opacity="0.72" />
    </svg>
  );
}

function renderMaterialImageGlyph(context: FileIconRenderContext): ReactNode {
  const tint = getMaterialTint(context.color);
  return (
    <svg width={context.size} height={context.size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <rect x="2.75" y="3.35" width="12.5" height="11" rx="1.8" fill={tint} stroke={context.color} strokeWidth="1.35" />
      <path d="M3.85 12.7 6.2 9.9l1.95 1.9 2.25-3 3.75 4" stroke={context.color} strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="11.95" cy="6.25" r="1.05" fill={context.color} />
    </svg>
  );
}

function renderMaterialVideoGlyph(context: FileIconRenderContext): ReactNode {
  const tint = getMaterialTint(context.color);
  return (
    <svg width={context.size} height={context.size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <rect x="2.75" y="4" width="12.5" height="10" rx="1.8" fill={tint} stroke={context.color} strokeWidth="1.35" />
      <path d="m7.25 6.6 4.7 2.4-4.7 2.4V6.6Z" fill={context.color} />
    </svg>
  );
}

function renderMaterialCodeGlyph(context: FileIconRenderContext): ReactNode {
  const tint = getMaterialTint(context.color);
  return (
    <svg width={context.size} height={context.size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <rect x="2.55" y="3.15" width="12.9" height="11.7" rx="2.05" fill={tint} />
      <path d="m7 5.9-3 3.1 3 3.1" stroke={context.color} strokeWidth="1.55" strokeLinecap="round" strokeLinejoin="round" />
      <path d="m11 5.9 3 3.1-3 3.1" stroke={context.color} strokeWidth="1.55" strokeLinecap="round" strokeLinejoin="round" />
      {context.kind === "html" && <path d="M9.95 5.6 8.05 12.4" stroke={context.color} strokeWidth="1.2" strokeLinecap="round" opacity="0.82" />}
    </svg>
  );
}

function renderMaterialSpreadsheetGlyph(context: FileIconRenderContext): ReactNode {
  const tint = getMaterialTint(context.color);
  return (
    <svg width={context.size} height={context.size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <rect x="3.5" y="2.9" width="11" height="12.2" rx="1.6" fill={tint} stroke={context.color} strokeWidth="1.25" />
      <path d="M3.6 6.8h10.8M3.6 10h10.8M7.15 3.1v11.8M10.85 3.1v11.8" stroke={context.color} strokeWidth="0.85" opacity="0.8" />
    </svg>
  );
}

function renderMaterialArchiveGlyph(context: FileIconRenderContext): ReactNode {
  const tint = getMaterialTint(context.color);
  return (
    <svg width={context.size} height={context.size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <path d="M3.4 6.1 9 3.05l5.6 3.05v5.8L9 14.95 3.4 11.9V6.1Z" fill={tint} stroke={context.color} strokeWidth="1.25" strokeLinejoin="round" />
      <path d="M3.65 6.25 9 9.15l5.35-2.9M9 9.15v5.45" stroke={context.color} strokeWidth="1.05" strokeLinejoin="round" opacity="0.8" />
    </svg>
  );
}

function renderMaterialDocumentGlyph(context: FileIconRenderContext): ReactNode {
  const tint = getMaterialTint(context.color);
  return (
    <svg width={context.size} height={context.size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <path
        d="M4.6 2.6h6.05l2.75 2.8v9c0 .55-.45 1-1 1H4.6c-.55 0-1-.45-1-1V3.6c0-.55.45-1 1-1Z"
        fill={tint}
        stroke={context.color}
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <path d="M10.65 2.8v2.6h2.55" stroke={context.color} strokeWidth="0.95" strokeLinejoin="round" />
      <MaterialDocumentSymbol kind={context.kind} color={context.color} />
    </svg>
  );
}

function MaterialDocumentSymbol({ kind, color }: { kind: FileVisualKind; color: string }) {
  const renderer = MATERIAL_DOCUMENT_SYMBOLS[kind] ?? (() => <DocumentLinesSymbol color={color} />);
  return renderer({ color });
}

const MATERIAL_DOCUMENT_SYMBOLS: Partial<Record<FileVisualKind, (context: { color: string }) => ReactNode>> = {
  json: ({ color }) => (
    <text x="8.8" y="12.2" textAnchor="middle" fontSize="8.3" fontWeight="850" fontFamily="var(--po-font-sans)" fill={color}>
      {"{}"}
    </text>
  ),
  pdf: ({ color }) => (
    <text x="8.75" y="11.65" textAnchor="middle" fontSize="4.4" fontWeight="850" fontFamily="var(--po-font-sans)" fill={color}>
      PDF
    </text>
  ),
};

function getMaterialTint(color: string): string {
  return `color-mix(in srgb, ${color} 16%, var(--po-panel-raised))`;
}

function renderMinimalGlyph(context: FileIconRenderContext): ReactNode {
  const Icon = getMinimalLucideIcon(context.kind);
  return <Icon size={context.size} color={context.color} strokeWidth={1.85} aria-hidden="true" />;
}

function renderStandaloneLinesGlyph({ color, size }: FileIconRenderContext): ReactNode {
  return <StandaloneDocumentLinesGlyph size={size} color={color} />;
}

function DocumentLinesSymbol({ color }: { color: string }) {
  return <path d="M5.4 7.55h6.25M5.4 9.55h6.25M5.4 11.55h4.3" stroke={color} strokeWidth="1.05" strokeLinecap="round" opacity="0.9" />;
}

function StandaloneDocumentLinesGlyph({ size, color }: { size: number; color: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <path d="M3.7 5.8h10.6M3.7 9h10.6M3.7 12.2h7.2" stroke={color} strokeWidth="1.7" strokeLinecap="round" opacity="0.92" />
    </svg>
  );
}

function getMinimalLucideIcon(kind: FileVisualKind): LucideIcon {
  const icons: Partial<Record<FileVisualKind, LucideIcon>> = {
    folder: LucideFolder,
    json: FileJson,
    html: FileCode,
    code: FileCode,
    image: FileImage,
    audio: FileAudio,
    video: FileVideo,
    spreadsheet: FileSpreadsheet,
    archive: FileArchive,
    markdown: FileText,
    text: FileText,
    document: FileText,
    pdf: FileText,
  };

  return icons[kind] ?? LucideFile;
}

function FolderGlyph({
  size,
  compact = false,
  theme = "default",
}: {
  size: number;
  compact?: boolean;
  theme?: FileIconThemeId;
}) {
  const strokeWidth = compact ? 1.7 : 1.45;

  const renderer = FOLDER_GLYPH_RENDERERS[theme] ?? FOLDER_GLYPH_RENDERERS.default;
  return renderer({ size, strokeWidth, compact });
}

type FolderGlyphContext = {
  size: number;
  strokeWidth: number;
  compact: boolean;
};

const FOLDER_GLYPH_RENDERERS: Record<"default" | "lines" | "vscode" | "material" | "minimal", (context: FolderGlyphContext) => ReactNode> = {
  default: renderDefaultFolderGlyph,
  lines: renderDefaultFolderGlyph,
  vscode: ({ size }) => <VsCodeFolderGlyph size={size} />,
  material: renderMaterialFolderGlyph,
  minimal: ({ size, compact }) => (
    <LucideFolder size={size} color="var(--po-file-accent-default)" strokeWidth={compact ? 1.85 : 1.65} aria-hidden="true" />
  ),
};

function renderDefaultFolderGlyph({ size, strokeWidth }: FolderGlyphContext): ReactNode {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M3.5 6.5c0-1.1.9-2 2-2h4.1l2 2h6.9c1.1 0 2 .9 2 2v8.5c0 1.1-.9 2-2 2h-13c-1.1 0-2-.9-2-2V6.5Z"
        fill="color-mix(in srgb, var(--po-file-icon-body) 68%, transparent)"
        stroke="var(--po-file-accent-default)"
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
      />
    </svg>
  );
}

function renderMaterialFolderGlyph({ size, strokeWidth, compact }: FolderGlyphContext): ReactNode {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M3 7.15c0-1.1.9-2 2-2h4.25l2 2H19c1.1 0 2 .9 2 2v7.55c0 1.1-.9 2-2 2H5c-1.1 0-2-.9-2-2V7.15Z"
        fill="color-mix(in srgb, var(--po-file-accent-default) 24%, var(--po-panel-raised))"
        stroke="var(--po-file-accent-default)"
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
      />
      <path
        d="M3.25 9.15h17.5"
        stroke="var(--po-file-accent-default)"
        strokeWidth={compact ? 1.35 : 1.15}
        strokeLinecap="round"
        opacity="0.56"
      />
    </svg>
  );
}
