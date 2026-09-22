import { StateEffect, StateField, type Range } from "@codemirror/state";
import {
  Decoration,
  type DecorationSet,
  EditorView,
  type Tooltip,
  ViewPlugin,
  type ViewUpdate,
  showTooltip,
} from "@codemirror/view";
import type { AiEditFile, AiEditHunk } from "../ai-edits/types";

type DeletedContentPreview = {
  key: string;
  kind: "removed" | "modified";
  anchorLine: number;
  anchorPos: number;
  oldText: string;
  hunkCount: number;
};

type DeletedContentTooltipState = {
  key: string;
  tooltip: Tooltip;
} | null;

const MAX_DELETED_PREVIEW_LINES = 8;
const MAX_DELETED_PREVIEW_LINE_CHARS = 180;
const DELETED_PREVIEW_TOOLTIP_WIDTH = 360;
const DELETED_PREVIEW_TOOLTIP_MARGIN = 14;
const MARKER_HITBOX_LEFT = -12;
const MARKER_HITBOX_RIGHT = 18;
const MARKER_HITBOX_TOP = -4;
const MARKER_HITBOX_HEIGHT = 36;

type AiEditLineMarkerKind = "added" | "removed" | "modified";

const setDeletedContentTooltip = StateEffect.define<DeletedContentTooltipState>();

const deletedContentTooltipField = StateField.define<DeletedContentTooltipState>({
  create() {
    return null;
  },
  update(value, transaction) {
    if (transaction.docChanged) return null;
    for (const effect of transaction.effects) {
      if (effect.is(setDeletedContentTooltip)) return effect.value;
    }
    return value;
  },
  provide(field) {
    return showTooltip.from(field, (value) => value?.tooltip ?? null);
  },
});

export function markdownAiEditExtension(aiEditFile: AiEditFile | null | undefined) {
  if (!aiEditFile || aiEditFile.hunks.length === 0) return [];
  const file = aiEditFile;
  return [
    deletedContentTooltipField,
    deletedContentHoverHandlers(file),
    ViewPlugin.fromClass(
      class {
        decorations: DecorationSet;

        constructor(view: EditorView) {
          this.decorations = safeBuildAiEditDecorations(view, file);
        }

        update(update: ViewUpdate) {
          if (update.docChanged || update.viewportChanged) {
            this.decorations = safeBuildAiEditDecorations(update.view, file);
          }
        }
      },
      {
        decorations: (plugin) => plugin.decorations,
      },
    ),
  ];
}

function deletedContentHoverHandlers(aiEditFile: AiEditFile) {
  return EditorView.domEventHandlers({
    mousemove(event, view) {
      const preview = getDeletedContentPreviewForMouseEvent(view, aiEditFile, event);
      setPreviewTooltipIfChanged(view, preview);
    },
    mouseleave(_event, view) {
      setPreviewTooltipIfChanged(view, null);
    },
  });
}

function safeBuildAiEditDecorations(view: EditorView, aiEditFile: AiEditFile): DecorationSet {
  try {
    return buildAiEditDecorations(view, aiEditFile);
  } catch (error) {
    console.warn("Unable to render AI edit decorations:", error);
    return Decoration.none;
  }
}

function buildAiEditDecorations(view: EditorView, aiEditFile: AiEditFile): DecorationSet {
  const ranges: Array<Range<Decoration>> = [];

  for (const hunk of aiEditFile.hunks) {
    if (hunk.state !== "pending") continue;

    if (hunk.newRange.lineCount === 0) {
      addLineDecoration(ranges, view, hunk, hunk.newRange.startLine, "removed", {
        markerKind: "removed",
      });
      continue;
    }

    const endLine = Math.min(view.state.doc.lines, hunk.newRange.startLine + hunk.newRange.lineCount - 1);
    const firstDecoratedLine = Math.max(1, hunk.newRange.startLine);
    for (let lineNumber = hunk.newRange.startLine; lineNumber <= endLine; lineNumber += 1) {
      if (lineNumber < 1) continue;
      addLineDecoration(ranges, view, hunk, lineNumber, hunk.kind, {
        markerKind: lineNumber === firstDecoratedLine ? hunk.kind : null,
      });
    }
  }

  return ranges.length > 0 ? Decoration.set(ranges, true) : Decoration.none;
}

function addLineDecoration(
  ranges: Array<Range<Decoration>>,
  view: EditorView,
  hunk: AiEditHunk,
  lineNumber: number,
  classKind: AiEditHunk["kind"] | "removed",
  marker: { markerKind: AiEditLineMarkerKind | null },
): void {
  const resolvedLineNumber = Math.max(1, Math.min(view.state.doc.lines, lineNumber));
  const line = view.state.doc.line(resolvedLineNumber);
  const markerClasses = marker.markerKind ? [`cm-ai-edit-line-marker-${marker.markerKind}`] : [];
  ranges.push(Decoration.line({
    class: ["cm-ai-edit-line", `cm-ai-edit-line-${classKind}`, ...markerClasses].join(" "),
    attributes: {
      title: getHunkTitle(hunk),
    },
  }).range(line.from));
}

function getHunkTitle(hunk: AiEditHunk): string {
  if (hunk.kind === "added") return "AI added this line";
  if (hunk.kind === "removed") return "AI removed nearby content";
  return "AI modified this line";
}

function getDeletedContentPreviewForMouseEvent(
  view: EditorView,
  aiEditFile: AiEditFile,
  event: MouseEvent,
): DeletedContentPreview | null {
  const pos = view.posAtCoords({ x: event.clientX, y: event.clientY }, false);
  const line = view.state.doc.lineAt(pos);
  const markerRect = getMarkerHitbox(view, line.number);
  if (!markerRect) return null;
  if (
    event.clientX < markerRect.left ||
    event.clientX > markerRect.right ||
    event.clientY < markerRect.top ||
    event.clientY > markerRect.bottom
  ) {
    return null;
  }

  const hunks = aiEditFile.hunks.filter((hunk) => {
    if (hunk.state !== "pending" || hunk.oldRange.lineCount === 0 || !hunk.oldText) return false;
    return getHunkAnchorLine(view, hunk) === line.number;
  });
  if (hunks.length === 0) return null;

  return {
    key: hunks.map((hunk) => hunk.id).join("|"),
    kind: hunks.every((hunk) => hunk.kind === "removed") ? "removed" : "modified",
    anchorLine: line.number,
    anchorPos: line.to,
    oldText: hunks.map((hunk) => hunk.oldText).join("\n"),
    hunkCount: hunks.length,
  };
}

function setPreviewTooltipIfChanged(view: EditorView, preview: DeletedContentPreview | null): void {
  const current = view.state.field(deletedContentTooltipField, false);
  const nextKey = preview?.key ?? null;
  const currentKey = current?.key ?? null;
  if (nextKey === currentKey) return;

  view.dispatch({
    effects: setDeletedContentTooltip.of(
      preview
        ? {
            key: preview.key,
            tooltip: createDeletedContentTooltip(preview),
          }
        : null,
    ),
  });
}

function createDeletedContentTooltip(preview: DeletedContentPreview): Tooltip {
  return {
    pos: preview.anchorPos,
    above: false,
    strictSide: false,
    arrow: false,
    clip: false,
    create(view) {
      const dom = document.createElement("div");
      dom.className = `cm-ai-edit-delete-preview-tooltip cm-ai-edit-delete-preview-tooltip--${preview.kind}`;

      const header = document.createElement("div");
      header.className = "cm-ai-edit-delete-preview-tooltip__header";
      header.textContent = getPreviewHeaderText(preview);
      dom.append(header);

      const body = document.createElement("div");
      body.className = "cm-ai-edit-delete-preview-tooltip__body";

      const { lines, truncated } = getDeletedPreviewLines(preview.oldText);
      for (const line of lines) {
        const row = document.createElement("div");
        row.className = "cm-ai-edit-delete-preview-tooltip__line";

        const code = document.createElement("code");
        code.textContent = line || " ";
        row.append(code);

        body.append(row);
      }

      dom.append(body);

      if (truncated) {
        const footer = document.createElement("div");
        footer.className = "cm-ai-edit-delete-preview-tooltip__footer";
        footer.textContent = "Open review for full diff";
        dom.append(footer);
      }

      return {
        dom,
        overlap: true,
        getCoords() {
          return getMarkerTooltipCoords(view, preview.anchorLine);
        },
      };
    },
  };
}

function getPreviewHeaderText(preview: DeletedContentPreview): string {
  if (preview.kind === "modified") return preview.hunkCount > 1 ? "Changed from" : "Before agent edit";
  return preview.hunkCount > 1 ? "Deleted content" : "Deleted by agent";
}

function getDeletedPreviewLines(oldText: string): { lines: string[]; truncated: boolean } {
  const rawLines = oldText.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const visibleLines = rawLines.slice(0, MAX_DELETED_PREVIEW_LINES).map((line) => (
    line.length > MAX_DELETED_PREVIEW_LINE_CHARS
      ? `${line.slice(0, MAX_DELETED_PREVIEW_LINE_CHARS)}...`
      : line
  ));
  const truncated = rawLines.length > MAX_DELETED_PREVIEW_LINES ||
    rawLines.some((line) => line.length > MAX_DELETED_PREVIEW_LINE_CHARS);
  return { lines: visibleLines, truncated };
}

function getHunkAnchorLine(view: EditorView, hunk: AiEditHunk): number {
  const targetLine = hunk.newRange.lineCount === 0
    ? hunk.newRange.startLine
    : Math.max(1, hunk.newRange.startLine);
  return Math.max(1, Math.min(view.state.doc.lines, targetLine));
}

function getMarkerHitbox(view: EditorView, lineNumber: number) {
  const lineElement = getLineElement(view, lineNumber);
  if (!lineElement) return null;
  const rect = lineElement.getBoundingClientRect();
  return {
    left: rect.right + MARKER_HITBOX_LEFT,
    right: rect.right + MARKER_HITBOX_RIGHT,
    top: rect.top + MARKER_HITBOX_TOP,
    bottom: rect.top + MARKER_HITBOX_HEIGHT,
  };
}

function getMarkerTooltipCoords(view: EditorView, lineNumber: number) {
  const viewRect = view.dom.getBoundingClientRect();
  const lineElement = getLineElement(view, lineNumber);
  if (!lineElement) {
    const fallbackLine = view.state.doc.line(Math.max(1, Math.min(view.state.doc.lines, lineNumber)));
    const fallbackCoords = view.coordsAtPos(fallbackLine.to, 1);
    if (fallbackCoords) {
      const fallbackX = clampTooltipX(fallbackCoords.left, viewRect);
      return {
        left: fallbackX,
        right: fallbackX,
        top: fallbackCoords.bottom + 6,
        bottom: fallbackCoords.bottom + 6,
      };
    }
    return {
      left: viewRect.left + DELETED_PREVIEW_TOOLTIP_MARGIN,
      right: viewRect.left + DELETED_PREVIEW_TOOLTIP_MARGIN,
      top: viewRect.top + DELETED_PREVIEW_TOOLTIP_MARGIN,
      bottom: viewRect.top + DELETED_PREVIEW_TOOLTIP_MARGIN,
    };
  }

  const rect = lineElement.getBoundingClientRect();
  const x = clampTooltipX(rect.right + 10, viewRect);
  const y = Math.min(rect.bottom + 8, viewRect.bottom - DELETED_PREVIEW_TOOLTIP_MARGIN);
  return {
    left: x,
    right: x,
    top: y,
    bottom: y,
  };
}

function clampTooltipX(anchorX: number, viewRect: DOMRect): number {
  const maxX = viewRect.right - DELETED_PREVIEW_TOOLTIP_WIDTH - DELETED_PREVIEW_TOOLTIP_MARGIN;
  const minX = viewRect.left + DELETED_PREVIEW_TOOLTIP_MARGIN;
  return Math.max(minX, Math.min(anchorX, maxX));
}

function getLineElement(view: EditorView, lineNumber: number): HTMLElement | null {
  const line = view.state.doc.line(Math.max(1, Math.min(view.state.doc.lines, lineNumber)));
  const domAtLine = view.domAtPos(line.from);
  const element = domAtLine.node.nodeType === Node.ELEMENT_NODE
    ? domAtLine.node as Element
    : domAtLine.node.parentElement;
  return element?.closest(".cm-line") as HTMLElement | null;
}
