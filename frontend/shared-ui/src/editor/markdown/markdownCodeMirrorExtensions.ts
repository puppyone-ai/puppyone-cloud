import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { HighlightStyle, bracketMatching, indentOnInput, syntaxHighlighting } from "@codemirror/language";
import { EditorSelection, EditorState, Facet, StateField, type Extension, type Range } from "@codemirror/state";
import {
  Decoration,
  type DecorationSet,
  EditorView,
  type Rect,
  WidgetType,
  dropCursor,
  highlightActiveLine,
  highlightSpecialChars,
  keymap,
  placeholder,
} from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { renderMarkdownInlineInto } from "./rendering/inlineRenderer";
import {
  getMarkdownTableBlock,
  isMarkdownTableLine,
  type MarkdownTableCell,
  type MarkdownTableRow,
} from "./rendering/tableModel";
import { getMarkdownTaskLine, type MarkdownTaskLine } from "./rendering/taskModel";
import { getMarkdownHtmlBlock, type MarkdownHtmlBlock } from "./rendering/htmlBlockModel";
import { isSafeHref } from "./rendering/markdownHtmlPolicy";
import { createSanitizedBlockHtmlFragment } from "./rendering/sanitizeHtml";
import { findMarkdownLinkTokens, isExternalMarkdownHref } from "./links/markdownLinkModel";
import { findWikiLinkTokens } from "./links/wikiLinkModel";
import { getHtmlPreviewInteractionCss } from "../htmlPreviewInteraction";
import type { MarkdownHtmlTrustMode, MarkdownLinkGraph } from "../viewerTypes";

type LivePreviewDecorations = {
  decorations: DecorationSet;
  atomicRanges: DecorationSet;
};

type MarkdownDecorationBuilders = {
  decorations: Range<Decoration>[];
  atomicRanges: Range<Decoration>[];
};

type OccupiedRange = {
  from: number;
  to: number;
};

type MarkdownCodeBlock = {
  from: number;
  to: number;
  nextLineNumber: number;
  language: string;
  code: string;
};

const markdownHtmlTrustModeFacet = Facet.define<MarkdownHtmlTrustMode, MarkdownHtmlTrustMode>({
  combine(values) {
    return values.length > 0 ? values[values.length - 1] : "safe";
  },
});

const markdownLinkGraphFacet = Facet.define<MarkdownLinkGraph | null, MarkdownLinkGraph | null>({
  combine(values) {
    return values.length > 0 ? values[values.length - 1] : null;
  },
});

const markdownDocumentPathFacet = Facet.define<string, string>({
  combine(values) {
    return values.length > 0 ? values[values.length - 1] : "";
  },
});

export function markdownCodeMirrorBaseExtensions(readOnly: boolean): Extension[] {
  return [
    highlightSpecialChars(),
    history(),
    dropCursor(),
    indentOnInput(),
    bracketMatching(),
    markdown({ base: markdownLanguage }),
    syntaxHighlighting(puppyMarkdownHighlightStyle),
    EditorView.lineWrapping,
    EditorView.contentAttributes.of({
      spellcheck: "false",
      autocorrect: "off",
      autocapitalize: "off",
    }),
    highlightActiveLine(),
    keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
    placeholder(readOnly ? "" : "Start writing..."),
    puppyMarkdownEditorTheme,
  ];
}

export function markdownLivePreviewExtension(
  htmlTrustMode: MarkdownHtmlTrustMode = "safe",
  markdownLinkGraph: MarkdownLinkGraph | null = null,
  documentPath = "",
): Extension {
  return [
    markdownHtmlTrustModeFacet.of(htmlTrustMode),
    markdownLinkGraphFacet.of(markdownLinkGraph),
    markdownDocumentPathFacet.of(documentPath),
    markdownLinkOpenHandler,
    markdownLivePreviewDecorations,
  ];
}

const puppyMarkdownEditorTheme = EditorView.theme({
  "&": {
    height: "100%",
    backgroundColor: "transparent",
    color: "inherit",
  },
  ".cm-scroller": {
    overflow: "auto",
  },
  ".cm-line": {
    padding: "0",
  },
  "&.cm-focused": {
    outline: "none",
  },
});

const puppyMarkdownHighlightStyle = HighlightStyle.define([
  { tag: tags.heading, class: "cm-md-syntax-heading" },
  { tag: [tags.heading1, tags.heading2, tags.heading3, tags.heading4, tags.heading5, tags.heading6], class: "cm-md-syntax-heading" },
  { tag: tags.strong, class: "cm-md-syntax-strong" },
  { tag: tags.emphasis, class: "cm-md-syntax-emphasis" },
  { tag: tags.strikethrough, class: "cm-md-syntax-strikethrough" },
  { tag: [tags.link, tags.url], class: "cm-md-syntax-link" },
  { tag: tags.monospace, class: "cm-md-syntax-monospace" },
  { tag: [tags.meta, tags.processingInstruction, tags.punctuation, tags.contentSeparator], class: "cm-md-syntax-markup" },
  { tag: tags.quote, class: "cm-md-syntax-quote" },
  { tag: tags.list, class: "cm-md-syntax-list" },
]);

const markdownLivePreviewDecorations = StateField.define<LivePreviewDecorations>({
  create(state) {
    return buildMarkdownDecorations(state);
  },
  update(decorations, transaction) {
    if (transaction.docChanged || transaction.reconfigured) {
      return buildMarkdownDecorations(transaction.state);
    }
    return {
      decorations: decorations.decorations.map(transaction.changes),
      atomicRanges: decorations.atomicRanges.map(transaction.changes),
    };
  },
  provide(field) {
    return [
      EditorView.decorations.from(field, (value) => value.decorations),
      EditorView.atomicRanges.of((view) => view.state.field(field).atomicRanges),
    ];
  },
});

let suppressNextMouseLinkClickUntil = 0;

const markdownLinkOpenHandler = EditorView.domEventHandlers({
  mousedown(event, view) {
    if (event.button !== 0) return false;
    const opened = openMarkdownLinkFromEvent(event, view);
    if (opened) suppressNextMouseLinkClickUntil = Date.now() + 700;
    return opened;
  },
  click(event, view) {
    if (
      event.detail > 0 &&
      suppressNextMouseLinkClickUntil >= Date.now() &&
      getMarkdownLinkElementFromEvent(event, view)
    ) {
      suppressNextMouseLinkClickUntil = 0;
      event.preventDefault();
      event.stopPropagation();
      return true;
    }
    return openMarkdownLinkFromEvent(event, view);
  },
  keydown(event, view) {
    if (event.key !== "Enter") return false;
    const linkElement = getMarkdownLinkElementFromEvent(event, view);
    if (!linkElement) return false;
    return openMarkdownLinkFromEvent(event, view);
  },
});

function openMarkdownLinkFromEvent(event: Event, view: EditorView): boolean {
  if (event.defaultPrevented) return false;
  const linkElement = getMarkdownLinkElementFromEvent(event, view);
  if (!linkElement) return false;

  const opened = openMarkdownLinkElement(linkElement, view);
  if (!opened) return false;

  event.preventDefault();
  event.stopPropagation();
  return true;
}

function getMarkdownLinkElementFromEvent(event: Event, view: EditorView): HTMLElement | null {
  const targetElement = getEventTargetElement(event.target);
  if (!targetElement) return null;

  const linkElement = targetElement.closest<HTMLElement>(
    ".cm-md-wiki-link-label[data-wiki-target], .cm-md-link-label[data-md-href]",
  );
  if (!linkElement || !view.dom.contains(linkElement)) return null;
  return linkElement;
}

function openMarkdownLinkElement(linkElement: HTMLElement, view: EditorView): boolean {
  const linkGraph = view.state.facet(markdownLinkGraphFacet);
  const sourcePath = view.state.facet(markdownDocumentPathFacet);
  const wikiTarget = linkElement.dataset.wikiTarget;
  if (wikiTarget) {
    if (!linkGraph?.openWikiLink) return false;

    const resolvedTarget = linkGraph.resolveWikiLink(sourcePath, wikiTarget);
    if (!resolvedTarget.exists && (!resolvedTarget.candidatePaths || resolvedTarget.candidatePaths.length === 0)) {
      return false;
    }

    linkGraph.openWikiLink(resolvedTarget, sourcePath);
    return true;
  }

  const href = linkElement.dataset.mdHref;
  if (!href) return false;

  if (isExternalMarkdownHref(href) && isSafeHref(href)) {
    return openExternalMarkdownHref(href, view);
  }

  const resolvedTarget = linkGraph?.resolveMarkdownLink(sourcePath, href) ?? null;
  if (!resolvedTarget || !linkGraph?.openWikiLink) return false;
  if (!resolvedTarget.exists && (!resolvedTarget.candidatePaths || resolvedTarget.candidatePaths.length === 0)) {
    return false;
  }

  linkGraph.openWikiLink(resolvedTarget, sourcePath);
  return true;
}

function openExternalMarkdownHref(href: string, view: EditorView): boolean {
  const linkGraph = view.state.facet(markdownLinkGraphFacet);
  if (linkGraph?.openExternalUrl) {
    linkGraph.openExternalUrl(href);
    return true;
  }

  window.open(href, "_blank", "noopener,noreferrer");
  return true;
}

function getEventTargetElement(target: EventTarget | null): Element | null {
  if (target instanceof Element) return target;
  if (target instanceof Node) return target.parentElement;
  return null;
}

function buildMarkdownDecorations(state: EditorState): LivePreviewDecorations {
  const builders: MarkdownDecorationBuilders = {
    decorations: [],
    atomicRanges: [],
  };

  addMarkdownBlockAndLineDecorations(
    state,
    builders,
    state.facet(markdownHtmlTrustModeFacet),
    state.facet(markdownLinkGraphFacet),
    state.facet(markdownDocumentPathFacet),
  );

  return {
    decorations: builders.decorations.length > 0 ? Decoration.set(builders.decorations, true) : Decoration.none,
    atomicRanges: builders.atomicRanges.length > 0 ? Decoration.set(builders.atomicRanges, true) : Decoration.none,
  };
}

function addMarkdownBlockAndLineDecorations(
  state: EditorState,
  builders: MarkdownDecorationBuilders,
  htmlTrustMode: MarkdownHtmlTrustMode,
  markdownLinkGraph: MarkdownLinkGraph | null,
  documentPath: string,
) {
  const lineCount = state.doc.lines;

  for (let lineNumber = 1; lineNumber <= lineCount;) {
    const line = state.doc.line(lineNumber);
    const codeBlock = getMarkdownCodeBlock(state, line.number);
    if (codeBlock) {
      addReplacementDecoration(
        builders,
        Decoration.replace({
          widget: new CodeBlockWidget(codeBlock.code, codeBlock.language, codeBlock.from, codeBlock.to),
          block: true,
        }),
        codeBlock.from,
        codeBlock.to,
      );
      lineNumber = codeBlock.nextLineNumber;
      continue;
    }

    const htmlBlock = getMarkdownHtmlBlock(state, line.number);
    if (htmlBlock) {
      addReplacementDecoration(
        builders,
        Decoration.replace({
          widget: new HtmlBlockWidget(htmlBlock, htmlTrustMode),
          block: true,
        }),
        htmlBlock.from,
        htmlBlock.to,
      );
      lineNumber = htmlBlock.nextLineNumber;
      continue;
    }

    const tableBlock = getMarkdownTableBlock(state, line.number);
    if (tableBlock) {
      addReplacementDecoration(
        builders,
        Decoration.replace({
          widget: new MarkdownTableWidget(tableBlock.rows, markdownLinkGraph, documentPath),
          block: true,
        }),
        tableBlock.from,
        tableBlock.to,
      );
      lineNumber = tableBlock.nextLineNumber;
      continue;
    }

    decorateMarkdownLine(line.from, line.to, line.text, builders, markdownLinkGraph, documentPath);
    lineNumber += 1;
  }
}

function decorateMarkdownLine(
  lineFrom: number,
  lineTo: number,
  text: string,
  builders: MarkdownDecorationBuilders,
  markdownLinkGraph: MarkdownLinkGraph | null,
  documentPath: string,
) {
  const taskLine = getMarkdownTaskLine({ from: lineFrom, to: lineTo, text });
  const lineClasses = getMarkdownLineClasses(text);
  if (lineClasses) {
    builders.decorations.push(
      Decoration.line({
        class: lineClasses,
        attributes: taskLine ? { style: `--md-list-depth:${taskLine.depth};` } : undefined,
      }).range(lineFrom),
    );
  }

  const hrMatch = /^(\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*)$/.exec(text);
  if (hrMatch && lineFrom < lineTo) {
    addReplacementDecoration(
      builders,
      Decoration.replace({
        widget: new HorizontalRuleWidget(),
        block: true,
      }),
      lineFrom,
      lineFrom + hrMatch[1].length,
    );
    return;
  }

  const headingMatch = /^(#{1,6})(\s|$)/.exec(text);
  if (headingMatch) {
    addHiddenDecoration(builders, lineFrom, lineFrom + headingMatch[0].length);
  }

  const blockquoteMarker = /^(\s*>\s?)/.exec(text);
  if (blockquoteMarker) {
    addHiddenDecoration(builders, lineFrom, lineFrom + blockquoteMarker[1].length);
  }

  if (taskLine) {
    addHiddenDecoration(builders, taskLine.prefixFrom, taskLine.prefixTo);
    builders.decorations.push(
      Decoration.widget({
        widget: new TaskCheckboxWidget(taskLine),
        side: -1,
      }).range(taskLine.prefixTo),
    );
    addInlineMarkdownDecorations(
      lineFrom,
      text,
      builders,
      markdownLinkGraph,
      documentPath,
      [{ from: taskLine.prefixFrom, to: taskLine.prefixTo }],
    );
    return;
  }

  const listMatch = /^(\s*)([-*+]|\d+[.)])\s+/.exec(text);
  if (listMatch) {
    addReplacementDecoration(
      builders,
      Decoration.replace({
        widget: new ListMarkerWidget(getListMarkerText(listMatch[2]), getListDepth(listMatch[1])),
        inclusive: false,
      }),
      lineFrom,
      lineFrom + listMatch[0].length,
    );
  }

  addInlineMarkdownDecorations(lineFrom, text, builders, markdownLinkGraph, documentPath);
}

function getMarkdownLineClasses(text: string): string {
  const classes: string[] = [];

  const headingMatch = /^(#{1,6})(?:\s|$)/.exec(text);
  if (headingMatch) {
    classes.push("cm-md-heading", `cm-md-heading-${headingMatch[1].length}`);
  }

  if (/^\s*>/.test(text)) classes.push("cm-md-blockquote");
  if (/^\s*(?:[-*+]|\d+[.)])\s+/.test(text)) classes.push("cm-md-list-line");
  if (/^\s*(?:[-*+]|\d+[.)])\s+\[[ xX]\]/.test(text)) classes.push("cm-md-task-line");
  if (/^\s*(?:[-*+]|\d+[.)])\s+\[[xX]\]/.test(text)) classes.push("cm-md-task-checked");
  if (/^\s*(`{3,}|~{3,})/.test(text)) classes.push("cm-md-code-fence");
  if (/^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/.test(text)) classes.push("cm-md-hr");
  if (isMarkdownTableLine(text)) classes.push("cm-md-table-line");

  return classes.join(" ");
}

function addInlineMarkdownDecorations(
  lineFrom: number,
  text: string,
  builders: MarkdownDecorationBuilders,
  markdownLinkGraph: MarkdownLinkGraph | null,
  documentPath: string,
  initialOccupied: OccupiedRange[] = [],
) {
  const occupied = [...initialOccupied];

  addDelimiterDecorations(lineFrom, text, /(`)([^`\n]+)(`)/g, 1, "cm-md-syntax-monospace", builders, occupied);
  addImageDecorations(lineFrom, text, builders, occupied);
  addWikiLinkDecorations(lineFrom, text, builders, occupied, markdownLinkGraph, documentPath);
  addLinkDecorations(lineFrom, text, builders, occupied, markdownLinkGraph, documentPath);
  addDelimiterDecorations(lineFrom, text, /(\*\*|__)(\S(?:.*?\S)?)\1/g, 1, "cm-md-syntax-strong", builders, occupied);
  addDelimiterDecorations(lineFrom, text, /(~~)(\S(?:.*?\S)?)(~~)/g, 1, "cm-md-syntax-strikethrough", builders, occupied);
  addItalicDecorations(lineFrom, text, builders, occupied);
}

function addImageDecorations(
  lineFrom: number,
  text: string,
  builders: MarkdownDecorationBuilders,
  occupied: OccupiedRange[],
) {
  const pattern = /!\[([^\]\n]*)\]\(([^)\n]+)\)/g;

  for (const match of text.matchAll(pattern)) {
    if (match.index == null) continue;
    const matchFrom = lineFrom + match.index;
    const matchTo = matchFrom + match[0].length;
    if (!reserveRange(occupied, matchFrom, matchTo)) continue;

    addReplacementDecoration(
      builders,
      Decoration.replace({
        widget: new ImagePreviewWidget(match[1], match[2]),
        inclusive: false,
      }),
      matchFrom,
      matchTo,
    );
  }
}

function addWikiLinkDecorations(
  lineFrom: number,
  text: string,
  builders: MarkdownDecorationBuilders,
  occupied: OccupiedRange[],
  markdownLinkGraph: MarkdownLinkGraph | null,
  documentPath: string,
) {
  for (const token of findWikiLinkTokens(text)) {
    const matchFrom = lineFrom + token.from;
    const matchTo = lineFrom + token.to;
    if (!reserveRange(occupied, matchFrom, matchTo)) continue;

    const visibleFrom = lineFrom + (token.aliasFrom ?? token.targetFrom);
    const visibleTo = lineFrom + (token.aliasTo ?? token.targetTo);
    if (visibleFrom >= visibleTo) continue;

    const resolvedTarget = markdownLinkGraph?.resolveWikiLink(documentPath, token.target) ?? null;
    const classes = [
      "cm-md-syntax-link",
      "cm-md-wiki-link-label",
      resolvedTarget?.exists ? "is-resolved" : "is-missing",
      resolvedTarget?.ambiguous ? "is-ambiguous" : "",
    ].filter(Boolean).join(" ");

    addHiddenDecoration(builders, matchFrom, visibleFrom);
    builders.decorations.push(
      Decoration.mark({
        class: classes,
        attributes: {
          "data-wiki-target": token.target,
          role: "link",
          tabindex: "0",
          "aria-label": getWikiLinkTitle(resolvedTarget, token.target),
        },
      }).range(visibleFrom, visibleTo),
    );
    addHiddenDecoration(builders, visibleTo, matchTo);
  }
}

function addLinkDecorations(
  lineFrom: number,
  text: string,
  builders: MarkdownDecorationBuilders,
  occupied: OccupiedRange[],
  markdownLinkGraph: MarkdownLinkGraph | null,
  documentPath: string,
) {
  for (const token of findMarkdownLinkTokens(text)) {
    const matchFrom = lineFrom + token.from;
    const labelFrom = lineFrom + token.labelFrom;
    const labelTo = lineFrom + token.labelTo;
    const matchTo = lineFrom + token.to;
    if (!reserveRange(occupied, matchFrom, matchTo)) continue;

    const resolvedTarget = markdownLinkGraph?.resolveMarkdownLink(documentPath, token.href) ?? null;
    const linkClasses = [
      "cm-md-syntax-link",
      "cm-md-link-label",
      resolvedTarget ? "cm-md-document-link-label" : "",
      resolvedTarget?.exists ? "is-resolved" : "",
      resolvedTarget && !resolvedTarget.exists ? "is-missing" : "",
      resolvedTarget?.ambiguous ? "is-ambiguous" : "",
      isExternalMarkdownHref(token.href) && isSafeHref(token.href) ? "is-external" : "",
    ].filter(Boolean).join(" ");

    addHiddenDecoration(builders, matchFrom, labelFrom);
    builders.decorations.push(
      Decoration.mark({
        class: linkClasses,
        attributes: {
          "data-md-href": token.href,
          role: "link",
          tabindex: "0",
          "aria-label": getMarkdownLinkTitle(resolvedTarget, token.href),
        },
      }).range(labelFrom, labelTo),
    );
    addHiddenDecoration(builders, labelTo, matchTo);
  }
}

function getWikiLinkTitle(
  resolvedTarget: ReturnType<MarkdownLinkGraph["resolveWikiLink"]> | null,
  target: string,
): string {
  if (!resolvedTarget) return target;
  if (!resolvedTarget.exists) return `Missing linked note: ${target}`;
  if (resolvedTarget.ambiguous) return `${resolvedTarget.path} (ambiguous title match)`;
  return resolvedTarget.path ?? target;
}

function getMarkdownLinkTitle(
  resolvedTarget: ReturnType<MarkdownLinkGraph["resolveMarkdownLink"]> | null,
  href: string,
): string {
  if (!resolvedTarget) return href;
  if (!resolvedTarget.exists) return `Missing linked note: ${href}`;
  if (resolvedTarget.ambiguous) return `${resolvedTarget.path} (ambiguous title match)`;
  return resolvedTarget.path ?? href;
}

function addDelimiterDecorations(
  lineFrom: number,
  text: string,
  pattern: RegExp,
  delimiterGroupIndex: number,
  contentClass: string,
  builders: MarkdownDecorationBuilders,
  occupied: OccupiedRange[],
) {
  for (const match of text.matchAll(pattern)) {
    if (match.index == null) continue;
    const delimiter = match[delimiterGroupIndex];
    const content = match[delimiterGroupIndex + 1];
    if (!delimiter || !content?.trim()) continue;

    const matchFrom = lineFrom + match.index;
    const openingTo = matchFrom + delimiter.length;
    const contentTo = openingTo + content.length;
    const closingTo = matchFrom + match[0].length;
    if (!reserveRange(occupied, matchFrom, closingTo)) continue;

    addHiddenDecoration(builders, matchFrom, openingTo);
    builders.decorations.push(Decoration.mark({ class: contentClass }).range(openingTo, contentTo));
    addHiddenDecoration(builders, contentTo, closingTo);
  }
}

function addItalicDecorations(
  lineFrom: number,
  text: string,
  builders: MarkdownDecorationBuilders,
  occupied: OccupiedRange[],
) {
  const pattern = /(^|[^\*])(\*)([^\s*](?:.*?[^\s*])?)(\*)(?!\*)/g;

  for (const match of text.matchAll(pattern)) {
    if (match.index == null) continue;
    const prefixLength = match[1].length;
    const content = match[3];
    if (!content?.trim()) continue;

    const openingFrom = lineFrom + match.index + prefixLength;
    const contentFrom = openingFrom + 1;
    const contentTo = contentFrom + content.length;
    const closingTo = contentTo + 1;
    if (!reserveRange(occupied, openingFrom, closingTo)) continue;

    addHiddenDecoration(builders, openingFrom, contentFrom);
    builders.decorations.push(Decoration.mark({ class: "cm-md-syntax-emphasis" }).range(contentFrom, contentTo));
    addHiddenDecoration(builders, contentTo, closingTo);
  }
}

function addHiddenDecoration(builders: MarkdownDecorationBuilders, from: number, to: number) {
  if (from >= to) return;
  addReplacementDecoration(
    builders,
    Decoration.replace({
      widget: hiddenMarkdownSyntaxWidget,
      inclusive: false,
    }),
    from,
    to,
  );
}

function addReplacementDecoration(
  builders: MarkdownDecorationBuilders,
  decoration: Decoration,
  from: number,
  to: number,
) {
  if (from >= to) return;
  const range = decoration.range(from, to);
  builders.decorations.push(range);
  builders.atomicRanges.push(range);
}

function reserveRange(occupied: OccupiedRange[], from: number, to: number): boolean {
  if (from >= to) return false;
  if (occupied.some((range) => from < range.to && to > range.from)) return false;
  occupied.push({ from, to });
  return true;
}

function getListMarkerText(marker: string): string {
  if (/^\d+[.)]$/.test(marker)) return marker;
  return "\u2022";
}

function getListDepth(leadingWhitespace: string): number {
  return Math.floor(leadingWhitespace.replace(/\t/g, "    ").length / 2);
}

function getMarkdownCodeBlock(state: EditorState, lineNumber: number): MarkdownCodeBlock | null {
  const doc = state.doc;
  const openingLine = doc.line(lineNumber);
  const openingMatch = /^(\s*)(`{3,}|~{3,})([^\n`]*)$/.exec(openingLine.text);
  if (!openingMatch) return null;

  const fence = openingMatch[2];
  const fenceCharacter = fence[0];
  const minimumFenceLength = fence.length;
  const language = openingMatch[3].trim().split(/\s+/)[0] ?? "";
  const codeLines: string[] = [];
  let closingLine = openingLine;
  let nextLineNumber = lineNumber + 1;

  while (nextLineNumber <= doc.lines) {
    const line = doc.line(nextLineNumber);
    const closingPattern = new RegExp(`^\\s*\\${fenceCharacter}{${minimumFenceLength},}\\s*$`);
    if (closingPattern.test(line.text)) {
      closingLine = line;
      nextLineNumber += 1;
      break;
    }

    codeLines.push(line.text);
    closingLine = line;
    nextLineNumber += 1;
  }

  return {
    from: openingLine.from,
    to: closingLine.to,
    nextLineNumber,
    language,
    code: codeLines.join("\n"),
  };
}

class ListMarkerWidget extends WidgetType {
  constructor(
    private readonly marker: string,
    private readonly depth: number,
  ) {
    super();
  }

  eq(widget: WidgetType): boolean {
    return widget instanceof ListMarkerWidget && widget.marker === this.marker && widget.depth === this.depth;
  }

  toDOM(): HTMLElement {
    const marker = document.createElement("span");
    marker.className = "cm-md-list-marker";
    marker.style.setProperty("--md-list-depth", String(this.depth));
    marker.textContent = this.marker;
    return marker;
  }

  coordsAt(dom: HTMLElement, pos: number, side: number): Rect | null {
    return getInlineWidgetTextCoords(dom, getInlineWidgetEdgeX(dom, pos, side));
  }
}

class HiddenMarkdownSyntaxWidget extends WidgetType {
  eq(widget: WidgetType): boolean {
    return widget instanceof HiddenMarkdownSyntaxWidget;
  }

  toDOM(): HTMLElement {
    const marker = document.createElement("span");
    marker.className = "cm-md-hidden-syntax-widget";
    marker.setAttribute("aria-hidden", "true");
    return marker;
  }

  coordsAt(dom: HTMLElement, pos: number, side: number): Rect | null {
    return getInlineWidgetTextCoords(dom, getInlineWidgetEdgeX(dom, pos, side));
  }
}

const hiddenMarkdownSyntaxWidget = new HiddenMarkdownSyntaxWidget();

class TaskCheckboxWidget extends WidgetType {
  private pointerDown: { x: number; y: number } | null = null;

  constructor(
    private readonly task: MarkdownTaskLine,
  ) {
    super();
  }

  eq(widget: WidgetType): boolean {
    return (
      widget instanceof TaskCheckboxWidget &&
      widget.task.checked === this.task.checked &&
      widget.task.depth === this.task.depth &&
      widget.task.checkboxFrom === this.task.checkboxFrom &&
      widget.task.checkboxTo === this.task.checkboxTo
    );
  }

  toDOM(view: EditorView): HTMLElement {
    const wrapper = document.createElement("span");
    wrapper.className = "cm-md-task-checkbox-widget";
    wrapper.style.setProperty("--md-list-depth", String(this.task.depth));

    const checkbox = document.createElement("span");
    checkbox.role = "checkbox";
    checkbox.className = this.task.checked ? "cm-md-task-checkbox is-checked" : "cm-md-task-checkbox";
    checkbox.setAttribute("aria-label", this.task.checked ? "Mark task incomplete" : "Mark task complete");
    checkbox.setAttribute("aria-checked", String(this.task.checked));

    checkbox.addEventListener("mousedown", (event) => {
      this.pointerDown = { x: event.clientX, y: event.clientY };
    });

    checkbox.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (this.pointerDown && hasPointerMoved(event, this.pointerDown)) return;
      if (view.state.readOnly) return;

      const nextValue = this.task.checked ? "[ ]" : "[x]";
      view.dispatch({
        changes: { from: this.task.checkboxFrom, to: this.task.checkboxTo, insert: nextValue },
        selection: EditorSelection.cursor(this.task.checkboxFrom + nextValue.length),
      });
      view.focus();
    });

    wrapper.appendChild(checkbox);
    return wrapper;
  }

  ignoreEvent() {
    return false;
  }

  coordsAt(dom: HTMLElement, pos: number, side: number): Rect | null {
    const line = dom.closest(".cm-line");
    const lineRect = line?.getBoundingClientRect();
    if (!line || !lineRect) return null;

    const lineStyle = window.getComputedStyle(line);
    const textLeft = lineRect.left + Number.parseFloat(lineStyle.paddingLeft || "0");
    return getInlineWidgetTextCoords(dom, textLeft);
  }
}

function getInlineWidgetEdgeX(dom: HTMLElement, pos: number, side: number): number {
  const rect = dom.getBoundingClientRect();
  return pos <= 0 || side < 0 ? rect.left : rect.right;
}

function getInlineWidgetTextCoords(dom: HTMLElement, x: number): Rect | null {
  const line = dom.closest(".cm-line");
  if (!(line instanceof HTMLElement)) return null;

  const referenceRect = dom.getBoundingClientRect();
  const textRect = getNearestVisibleTextRect(line, referenceRect) ?? getFallbackLineTextRect(line);

  return {
    left: x,
    right: x,
    top: textRect.top,
    bottom: textRect.bottom,
  };
}

function getNearestVisibleTextRect(line: HTMLElement, referenceRect: DOMRect): Rect | null {
  const ownerDocument = line.ownerDocument;
  const ownerWindow = ownerDocument.defaultView;
  if (!ownerWindow) return null;

  const textNodes = ownerDocument.createTreeWalker(
    line,
    ownerWindow.NodeFilter.SHOW_TEXT,
    {
      acceptNode(node) {
        if (!node.nodeValue?.trim()) return ownerWindow.NodeFilter.FILTER_REJECT;
        const parent = node.parentElement;
        if (!parent) return ownerWindow.NodeFilter.FILTER_REJECT;
        if (parent.closest(".cm-md-hidden-syntax-widget, .cm-md-task-checkbox-widget")) {
          return ownerWindow.NodeFilter.FILTER_REJECT;
        }
        return ownerWindow.NodeFilter.FILTER_ACCEPT;
      },
    },
  );

  const referenceY = referenceRect.top + referenceRect.height / 2;
  const referenceX = referenceRect.left + referenceRect.width / 2;
  let bestRect: Rect | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;

  for (let node = textNodes.nextNode(); node; node = textNodes.nextNode()) {
    const range = ownerDocument.createRange();
    range.selectNodeContents(node);
    for (const rect of Array.from(range.getClientRects())) {
      if (rect.width <= 0 || rect.height <= 0) continue;

      const verticalDistance = Math.abs(rect.top + rect.height / 2 - referenceY);
      const horizontalDistance = referenceX < rect.left
        ? rect.left - referenceX
        : referenceX > rect.right
          ? referenceX - rect.right
          : 0;
      const distance = verticalDistance * 4 + horizontalDistance;

      if (distance < bestDistance) {
        bestDistance = distance;
        bestRect = {
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
        };
      }
    }
    range.detach();
  }

  return bestRect;
}

function getFallbackLineTextRect(line: HTMLElement): Rect {
  const lineRect = line.getBoundingClientRect();
  const style = window.getComputedStyle(line);
  const paddingTop = parseCssPixelValue(style.paddingTop);
  const lineHeight = parseCssPixelValue(style.lineHeight) || parseCssPixelValue(style.fontSize) * 1.2;
  const top = lineRect.top + paddingTop;
  return {
    top,
    bottom: top + lineHeight,
    left: lineRect.left,
    right: lineRect.right,
  };
}

function parseCssPixelValue(value: string): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

class HorizontalRuleWidget extends WidgetType {
  eq(widget: WidgetType): boolean {
    return widget instanceof HorizontalRuleWidget;
  }

  toDOM(): HTMLElement {
    const rule = document.createElement("div");
    rule.className = "cm-md-hr-widget";
    return rule;
  }
}

class HtmlBlockWidget extends WidgetType {
  private messageListener: ((event: MessageEvent) => void) | null = null;
  private readyTimer: number | null = null;

  constructor(
    private readonly block: MarkdownHtmlBlock,
    private readonly htmlTrustMode: MarkdownHtmlTrustMode,
  ) {
    super();
  }

  eq(widget: WidgetType): boolean {
    return (
      widget instanceof HtmlBlockWidget &&
      widget.block.source === this.block.source &&
      widget.block.tagName === this.block.tagName &&
      widget.block.closed === this.block.closed &&
      widget.htmlTrustMode === this.htmlTrustMode
    );
  }

  toDOM(): HTMLElement {
    const shell = document.createElement("div");
    shell.className = "cm-md-html-widget";

    const toolbar = document.createElement("div");
    toolbar.className = "cm-md-html-widget-toolbar";

    const toggleButton = document.createElement("button");
    toggleButton.className = "cm-md-html-source-toggle";
    toggleButton.type = "button";
    toolbar.appendChild(toggleButton);

    const content = document.createElement("div");
    content.className = "cm-md-html-widget-content";

    let showingSource = false;
    const render = () => {
      this.clearPreviewLifecycle();
      content.replaceChildren(showingSource ? createHtmlSourceBlock(this.block.source) : this.createPreviewBlock());
      toggleButton.replaceChildren(createHtmlWidgetIcon(showingSource ? "preview" : "source"));
      toggleButton.title = showingSource ? "Show HTML preview" : "Show HTML source";
      toggleButton.setAttribute("aria-label", showingSource ? "Show HTML preview" : "Show HTML source");
      toggleButton.classList.toggle("active", showingSource);
    };

    toggleButton.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      showingSource = !showingSource;
      render();
    });

    render();
    shell.append(toolbar, content);
    return shell;
  }

  private createPreviewBlock(): HTMLElement {
    if (!this.block.closed) {
      return createUnsupportedHtmlBlock(this.block, ["HTML block is not closed"]);
    }

    if (this.htmlTrustMode === "localTrusted") {
      return this.createTrustedHtmlBlock(this.block);
    }

    const result = createSanitizedBlockHtmlFragment(this.block.source);
    if (!result.supported) {
      return createUnsupportedHtmlBlock(this.block, result.reasons);
    }

    const wrapper = document.createElement("div");
    wrapper.className = "cm-md-html-rendered-surface cm-md-html-block";
    wrapper.appendChild(result.fragment);
    return wrapper;
  }

  destroy() {
    this.clearPreviewLifecycle();
  }

  private clearPreviewLifecycle() {
    if (this.messageListener) {
      window.removeEventListener("message", this.messageListener);
      this.messageListener = null;
    }
    if (this.readyTimer !== null) {
      window.clearTimeout(this.readyTimer);
      this.readyTimer = null;
    }
  }

  ignoreEvent() {
    return true;
  }

  private createTrustedHtmlBlock(block: MarkdownHtmlBlock): HTMLElement {
    const wrapper = document.createElement("div");
    wrapper.className = "cm-md-html-trusted-block is-loading";

    const sizer = createTrustedHtmlSizer(block.source);
    const loader = createTrustedHtmlLoader();
    wrapper.appendChild(sizer);
    wrapper.appendChild(loader);

    const frameId = createTrustedHtmlFrameId();
    const iframe = document.createElement("iframe");
    iframe.className = "cm-md-html-trusted-frame";
    iframe.title = "Trusted Markdown HTML preview";
    iframe.sandbox.add("allow-downloads", "allow-forms", "allow-modals", "allow-popups", "allow-scripts");
    iframe.referrerPolicy = "no-referrer";
    iframe.style.height = `${estimateTrustedHtmlFrameHeight(block.source)}px`;

    const markReady = () => {
      if (this.readyTimer !== null) {
        window.clearTimeout(this.readyTimer);
        this.readyTimer = null;
      }
      if (!wrapper.classList.contains("is-loading")) return;
      wrapper.classList.remove("is-loading");
      sizer.remove();
      loader.remove();
    };

    let measuredHeight = false;
    this.messageListener = (event: MessageEvent) => {
      if (event.source !== iframe.contentWindow) return;
      if (!isTrustedHtmlHeightMessage(event.data, frameId)) return;
      measuredHeight = true;
      iframe.style.height = `${clampNumber(event.data.height, 80, 2400)}px`;
      markReady();
    };
    window.addEventListener("message", this.messageListener);

    iframe.addEventListener("load", () => {
      if (!wrapper.classList.contains("is-loading")) return;
      this.readyTimer = window.setTimeout(() => {
        if (!measuredHeight) markReady();
      }, 120);
    }, { once: true });

    iframe.srcdoc = createTrustedHtmlDocument(block.source, frameId);
    wrapper.appendChild(iframe);
    return wrapper;
  }
}

function createHtmlSourceBlock(source: string): HTMLElement {
  const pre = document.createElement("pre");
  pre.className = "cm-md-html-source-block";

  const code = document.createElement("code");
  code.textContent = source;
  pre.appendChild(code);

  return pre;
}

function createTrustedHtmlSizer(source: string): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.className = "cm-md-html-rendered-surface cm-md-html-trusted-sizer";
  wrapper.setAttribute("aria-hidden", "true");

  const result = createSanitizedBlockHtmlFragment(source);
  if (result.fragment.childNodes.length > 0) {
    wrapper.appendChild(result.fragment);
    return wrapper;
  }

  const placeholder = document.createElement("div");
  placeholder.className = "cm-md-html-sizing-placeholder";
  wrapper.appendChild(placeholder);
  return wrapper;
}

function createTrustedHtmlLoader(): HTMLElement {
  const loader = document.createElement("div");
  loader.className = "cm-md-html-trusted-loader";
  loader.setAttribute("aria-hidden", "true");

  for (let index = 0; index < 3; index += 1) {
    const line = document.createElement("span");
    loader.appendChild(line);
  }

  return loader;
}

function createHtmlWidgetIcon(kind: "preview" | "source"): SVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "13");
  svg.setAttribute("height", "13");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");

  const paths = kind === "preview"
    ? [
        ["path", "M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"],
        ["circle", "M12 12", "3"],
      ] as const
    : [
        ["polyline", "16 18 22 12 16 6"],
        ["polyline", "8 6 2 12 8 18"],
      ] as const;

  for (const item of paths) {
    if (item[0] === "circle") {
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", "12");
      circle.setAttribute("cy", "12");
      circle.setAttribute("r", item[2]);
      svg.appendChild(circle);
      continue;
    }

    const element = document.createElementNS("http://www.w3.org/2000/svg", item[0]);
    if (item[0] === "path") element.setAttribute("d", item[1]);
    else element.setAttribute("points", item[1]);
    svg.appendChild(element);
  }

  return svg;
}

function createTrustedHtmlDocument(source: string, frameId: string): string {
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<base target="_blank">
<style>
${getTrustedHtmlThemeCss()}
* {
  box-sizing: border-box;
}
html {
  min-height: 0;
  color-scheme: light dark;
  background: transparent;
}
body {
  margin: 0;
  overflow: hidden;
  background: transparent;
  color: var(--text-normal);
  font-family: var(--font-text);
  font-size: 14px;
  line-height: 1.6;
}
#puppyone-md-html-content {
  display: flow-root;
  min-height: 0;
}
a {
  color: var(--text-accent);
  text-decoration: none;
}
a:hover {
  text-decoration: underline;
}
img,
video,
canvas,
svg {
  max-width: 100%;
}
pre,
code {
  font-family: var(--font-monospace);
}
${getHtmlPreviewInteractionCss("#puppyone-md-html-content")}
</style>
</head>
<body>
<div id="puppyone-md-html-content">
${source}
</div>
<script>
(() => {
  const frameId = ${JSON.stringify(frameId)};
  const postHeight = () => {
    const content = document.getElementById("puppyone-md-html-content");
    if (!content) return;
    const rect = content.getBoundingClientRect();
    const height = Math.ceil(Math.max(content.scrollHeight, rect.height));
    parent.postMessage({ type: "puppyone:markdown-html-height", id: frameId, height }, "*");
  };
  addEventListener("load", postHeight);
  if ("ResizeObserver" in window) {
    const content = document.getElementById("puppyone-md-html-content");
    if (content) new ResizeObserver(postHeight).observe(content);
  }
  requestAnimationFrame(postHeight);
  setTimeout(postHeight, 120);
})();
</script>
</body>
</html>`;
}

function getTrustedHtmlThemeCss(): string {
  const rootStyle = getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string) => rootStyle.getPropertyValue(name).trim() || fallback;

  return `:root {
  --background-primary: ${read("--po-editor-bg", "#ffffff")};
  --background-primary-alt: ${read("--po-panel", "#f7f3ec")};
  --background-modifier-border: ${read("--po-divider", "#ded4c7")};
  --text-normal: ${read("--po-text", "#2f2a24")};
  --text-muted: ${read("--po-text-muted", "#8a8073")};
  --text-accent: ${read("--po-accent", "#2563eb")};
  --font-text: ${read("--po-font-sans", "ui-sans-serif, system-ui, sans-serif")};
  --font-monospace: ${read("--po-font-mono", "ui-monospace, SFMono-Regular, Menlo, monospace")};
}`;
}

function createTrustedHtmlFrameId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }

  return `md-html-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function estimateTrustedHtmlFrameHeight(source: string): number {
  return source.trim() ? 160 : 80;
}

function isTrustedHtmlHeightMessage(
  value: unknown,
  frameId: string,
): value is { type: "puppyone:markdown-html-height"; id: string; height: number } {
  if (!value || typeof value !== "object") return false;
  const message = value as { type?: unknown; id?: unknown; height?: unknown };
  return (
    message.type === "puppyone:markdown-html-height" &&
    message.id === frameId &&
    typeof message.height === "number" &&
    Number.isFinite(message.height)
  );
}

function clampNumber(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function createUnsupportedHtmlBlock(block: MarkdownHtmlBlock, reasons: string[]): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.className = "cm-md-html-unsupported";

  const title = document.createElement("strong");
  title.textContent = "Unsupported HTML";
  wrapper.appendChild(title);

  const detail = document.createElement("span");
  detail.textContent = reasons[0] ?? `<${block.tagName}> is not supported in Markdown preview`;
  wrapper.appendChild(detail);

  const code = document.createElement("code");
  code.textContent = getHtmlPreviewSnippet(block.source);
  wrapper.appendChild(code);

  return wrapper;
}

function getHtmlPreviewSnippet(source: string): string {
  const normalized = source.trim().replace(/\s+/g, " ");
  if (normalized.length <= 140) return normalized;
  return `${normalized.slice(0, 137)}...`;
}

class CodeBlockWidget extends WidgetType {
  constructor(
    private readonly code: string,
    private readonly language: string,
    private readonly from: number,
    private readonly to: number,
  ) {
    super();
  }

  eq(widget: WidgetType): boolean {
    return (
      widget instanceof CodeBlockWidget &&
      widget.code === this.code &&
      widget.language === this.language &&
      widget.from === this.from &&
      widget.to === this.to
    );
  }

  toDOM(view: EditorView): HTMLElement {
    const wrapper = document.createElement("div");
    wrapper.className = "cm-md-code-widget";
    const panel = document.createElement("div");
    panel.className = "cm-md-code-panel";
    const readOnly = view.state.readOnly;
    let committed = false;
    const commit = () => {
      if (committed) return;
      committed = true;
      this.commitCodeBlockChange(view, languageInput.value, codeEditor.value);
    };

    const languageInput = document.createElement("input");
    languageInput.className = "cm-md-code-language";
    if (!this.language) languageInput.classList.add("is-empty");
    languageInput.value = this.language;
    languageInput.placeholder = "language";
    languageInput.readOnly = readOnly;
    languageInput.spellcheck = false;
    languageInput.addEventListener("mousedown", stopCodeMirrorEvent);
    languageInput.addEventListener("click", stopCodeMirrorEvent);
    languageInput.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Enter") {
        event.preventDefault();
        languageInput.blur();
      }
      if (event.key === "Escape") {
        event.preventDefault();
        languageInput.value = this.language;
        languageInput.blur();
      }
    });
    languageInput.addEventListener("blur", () => {
      if (readOnly) return;
      commit();
    });
    panel.appendChild(languageInput);

    const codeEditor = document.createElement("textarea");
    codeEditor.className = "cm-md-code-textarea";
    codeEditor.value = this.code;
    codeEditor.readOnly = readOnly;
    codeEditor.spellcheck = false;
    codeEditor.rows = Math.max(1, this.code.split("\n").length);
    codeEditor.addEventListener("mousedown", stopCodeMirrorEvent);
    codeEditor.addEventListener("click", stopCodeMirrorEvent);
    codeEditor.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Escape") {
        event.preventDefault();
        codeEditor.value = this.code;
        codeEditor.blur();
      }
    });
    codeEditor.addEventListener("blur", () => {
      if (readOnly) return;
      commit();
    });
    panel.appendChild(codeEditor);
    wrapper.appendChild(panel);

    return wrapper;
  }

  private commitCodeBlockChange(view: EditorView, nextLanguage: string, nextCode: string) {
    const language = sanitizeCodeLanguage(nextLanguage);
    const code = normalizeLineEndings(nextCode);
    if (language === this.language && code === this.code) return;

    view.dispatch({
      changes: {
        from: this.from,
        to: this.to,
        insert: serializeMarkdownCodeBlock(language, code),
      },
    });
  }

  ignoreEvent() {
    return true;
  }
}

class ImagePreviewWidget extends WidgetType {
  constructor(
    private readonly alt: string,
    private readonly source: string,
  ) {
    super();
  }

  eq(widget: WidgetType): boolean {
    return widget instanceof ImagePreviewWidget && widget.alt === this.alt && widget.source === this.source;
  }

  toDOM(view: EditorView): HTMLElement {
    const wrapper = document.createElement("span");
    wrapper.className = "cm-md-image-widget";
    wrapper.title = this.source;

    if (/^(https?:|data:|blob:)/i.test(this.source)) {
      const image = document.createElement("img");
      image.src = this.source;
      image.alt = this.alt;
      image.loading = "lazy";
      image.addEventListener("load", () => view.requestMeasure());
      image.addEventListener("error", () => view.requestMeasure());
      wrapper.appendChild(image);
      return wrapper;
    }

    const label = document.createElement("span");
    label.className = "cm-md-image-placeholder";
    label.textContent = this.alt || this.source;
    wrapper.appendChild(label);
    return wrapper;
  }
}

class MarkdownTableWidget extends WidgetType {
  constructor(
    private readonly rows: MarkdownTableRow[],
    private readonly markdownLinkGraph: MarkdownLinkGraph | null,
    private readonly documentPath: string,
  ) {
    super();
  }

  eq(widget: WidgetType): boolean {
    return (
      widget instanceof MarkdownTableWidget &&
      JSON.stringify(widget.rows) === JSON.stringify(this.rows) &&
      widget.markdownLinkGraph === this.markdownLinkGraph &&
      widget.documentPath === this.documentPath
    );
  }

  toDOM(view: EditorView): HTMLElement {
    const wrapper = document.createElement("div");
    wrapper.className = "cm-md-table-widget-wrap";

    const table = document.createElement("table");
    table.className = "cm-md-table-widget";

    const header = this.rows.find((row) => row.header);
    if (header) {
      const thead = document.createElement("thead");
      const tr = document.createElement("tr");
      for (const cell of header.cells) {
        const th = document.createElement("th");
        th.appendChild(createTableCellEditor(view, cell, this.markdownLinkGraph, this.documentPath));
        tr.appendChild(th);
      }
      thead.appendChild(tr);
      table.appendChild(thead);
    }

    const bodyRows = this.rows.filter((row) => !row.header);
    if (bodyRows.length > 0) {
      const tbody = document.createElement("tbody");
      for (const row of bodyRows) {
        const tr = document.createElement("tr");
        for (const cell of row.cells) {
          const td = document.createElement("td");
          td.appendChild(createTableCellEditor(view, cell, this.markdownLinkGraph, this.documentPath));
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
    }

    wrapper.appendChild(table);

    return wrapper;
  }

  ignoreEvent() {
    return true;
  }
}

function createTableCellEditor(
  view: EditorView,
  cell: MarkdownTableCell,
  markdownLinkGraph: MarkdownLinkGraph | null,
  documentPath: string,
): HTMLElement {
  const content = document.createElement("span");
  content.className = "cm-md-table-cell-content";
  content.spellcheck = false;
  renderTableCellPreview(content, cell.text, markdownLinkGraph, documentPath);

  if (!view.state.readOnly && cell.editable) {
    let editing = false;
    content.contentEditable = "true";
    content.addEventListener("focus", () => {
      if (editing) return;
      editing = true;
      content.textContent = cell.text;
    });
    content.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Enter") {
        event.preventDefault();
        content.blur();
      }
      if (event.key === "Escape") {
        event.preventDefault();
        content.textContent = cell.text;
        content.blur();
      }
    });
    content.addEventListener("blur", () => {
      const nextValue = sanitizeMarkdownTableCell(content.textContent ?? "");
      editing = false;
      if (nextValue === cell.text) {
        renderTableCellPreview(content, cell.text, markdownLinkGraph, documentPath);
        view.requestMeasure();
        return;
      }
      view.dispatch({
        changes: {
          from: cell.from,
          to: cell.to,
          insert: nextValue,
        },
      });
    });
  }

  content.addEventListener("mousedown", stopCodeMirrorEvent);
  content.addEventListener("click", stopCodeMirrorEvent);
  content.addEventListener("input", stopCodeMirrorEvent);

  return content;
}

function renderTableCellPreview(
  content: HTMLElement,
  source: string,
  markdownLinkGraph: MarkdownLinkGraph | null,
  documentPath: string,
) {
  content.replaceChildren();
  renderMarkdownInlineInto(content, source, { markdownLinkGraph, sourcePath: documentPath });
}

function stopCodeMirrorEvent(event: Event) {
  event.stopPropagation();
}

function sanitizeMarkdownTableCell(value: string): string {
  return normalizeLineEndings(value).replace(/\n+/g, " ").replace(/\|/g, "\\|").trim();
}

function sanitizeCodeLanguage(value: string): string {
  return value.trim().replace(/\s+/g, "-").replace(/[`~]/g, "");
}

function hasPointerMoved(event: MouseEvent, pointerDown: { x: number; y: number }): boolean {
  return Math.abs(event.clientX - pointerDown.x) > 4 || Math.abs(event.clientY - pointerDown.y) > 4;
}

function normalizeLineEndings(value: string): string {
  return value.replace(/\r\n?/g, "\n");
}

function serializeMarkdownCodeBlock(language: string, code: string): string {
  const longestFence = Math.max(2, ...Array.from(code.matchAll(/`+/g), (match) => match[0].length));
  const fence = "`".repeat(Math.max(3, longestFence + 1));
  const info = language ? `${fence}${language}` : fence;
  return `${info}\n${code}\n${fence}`;
}
