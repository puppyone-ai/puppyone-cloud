import { isSafeHref } from "./markdownHtmlPolicy";
import { appendSanitizedInlineHtml } from "./sanitizeHtml";
import type { MarkdownLinkGraph } from "../../viewerTypes";
import { findWikiLinkTokens, type MarkdownWikiLinkToken } from "../links/wikiLinkModel";

type InlineToken =
  | { kind: "code"; from: number; to: number; text: string }
  | { kind: "del" | "em" | "strong"; from: number; to: number; text: string }
  | { kind: "link"; from: number; to: number; label: string; href: string }
  | { kind: "wikiLink"; from: number; to: number; token: MarkdownWikiLinkToken };

export type MarkdownInlineRenderOptions = {
  markdownLinkGraph?: MarkdownLinkGraph | null;
  sourcePath?: string;
};

export function createMarkdownInlineFragment(
  source: string,
  options: MarkdownInlineRenderOptions = {},
): DocumentFragment {
  const fragment = document.createDocumentFragment();
  renderMarkdownInlineInto(fragment, source, options);
  return fragment;
}

export function renderMarkdownInlineInto(
  target: Node,
  source: string,
  options: MarkdownInlineRenderOptions = {},
) {
  if (!source) return;

  if (mightContainHtml(source)) {
    const template = document.createElement("template");
    template.innerHTML = source;
    appendSanitizedInlineHtml(target, template.content, (node, text) => appendMarkdownText(node, text, options));
    return;
  }

  appendMarkdownText(target, source, options);
}

function appendMarkdownText(target: Node, source: string, options: MarkdownInlineRenderOptions) {
  let cursor = 0;

  while (cursor < source.length) {
    const token = findNextToken(source, cursor);
    if (!token) {
      appendText(target, source.slice(cursor));
      break;
    }

    if (token.from > cursor) {
      appendText(target, source.slice(cursor, token.from));
    }

    appendToken(target, token, options);
    cursor = token.to;
  }
}

function appendToken(target: Node, token: InlineToken, options: MarkdownInlineRenderOptions) {
  if (token.kind === "code") {
    const code = document.createElement("code");
    code.className = "cm-md-inline-code";
    code.textContent = token.text;
    target.appendChild(code);
    return;
  }

  if (token.kind === "wikiLink") {
    appendWikiLink(target, token.token, options);
    return;
  }

  if (token.kind === "link") {
    if (!isSafeHref(token.href)) {
      appendText(target, token.label);
      return;
    }

    const link = document.createElement("a");
    link.href = token.href;
    link.rel = "noreferrer noopener";
    link.target = "_blank";
    renderMarkdownInlineInto(link, token.label);
    target.appendChild(link);
    return;
  }

  const element = document.createElement(token.kind);
  renderMarkdownInlineInto(element, token.text, options);
  target.appendChild(element);
}

function appendWikiLink(target: Node, token: MarkdownWikiLinkToken, options: MarkdownInlineRenderOptions) {
  const linkGraph = options.markdownLinkGraph ?? null;
  const sourcePath = options.sourcePath ?? "";
  const resolvedTarget = linkGraph?.resolveWikiLink(sourcePath, token.target) ?? null;

  if (!resolvedTarget?.exists) {
    const missing = document.createElement("span");
    missing.className = "cm-md-wiki-link-inline is-missing";
    missing.textContent = token.label;
    missing.title = `Missing linked note: ${token.target}`;
    target.appendChild(missing);
    return;
  }

  const link = document.createElement("a");
  link.href = "#";
  link.className = resolvedTarget.ambiguous
    ? "cm-md-wiki-link-inline is-resolved is-ambiguous"
    : "cm-md-wiki-link-inline is-resolved";
  link.textContent = token.label;
  link.title = resolvedTarget.path ?? token.target;
  link.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    linkGraph?.openWikiLink?.(resolvedTarget, sourcePath);
  });
  target.appendChild(link);
}

function appendText(target: Node, text: string) {
  if (text) target.appendChild(document.createTextNode(text));
}

function findNextToken(source: string, start: number): InlineToken | null {
  for (let index = start; index < source.length; index += 1) {
    const token =
      parseCodeToken(source, index) ??
      parseWikiLinkToken(source, index) ??
      parseLinkToken(source, index) ??
      parseDelimitedToken(source, index, "**", "strong") ??
      parseDelimitedToken(source, index, "__", "strong") ??
      parseDelimitedToken(source, index, "~~", "del") ??
      parseEmphasisToken(source, index, "*") ??
      parseEmphasisToken(source, index, "_");

    if (token) return token;
  }

  return null;
}

function parseWikiLinkToken(source: string, from: number): InlineToken | null {
  if (!source.startsWith("[[", from)) return null;
  if (isEscaped(source, from)) return null;
  const token = findWikiLinkTokens(source.slice(from))[0] ?? null;
  if (!token || token.from !== 0) return null;
  const shiftedToken = {
    ...token,
    from: token.from + from,
    to: token.to + from,
    openingFrom: token.openingFrom + from,
    openingTo: token.openingTo + from,
    targetFrom: token.targetFrom + from,
    targetTo: token.targetTo + from,
    aliasFrom: token.aliasFrom === null ? null : token.aliasFrom + from,
    aliasTo: token.aliasTo === null ? null : token.aliasTo + from,
    closingFrom: token.closingFrom + from,
    closingTo: token.closingTo + from,
  };
  return {
    kind: "wikiLink",
    from: shiftedToken.from,
    to: shiftedToken.to,
    token: shiftedToken,
  };
}

function parseCodeToken(source: string, from: number): InlineToken | null {
  if (source[from] !== "`") return null;
  const to = source.indexOf("`", from + 1);
  if (to <= from + 1) return null;
  return {
    kind: "code",
    from,
    to: to + 1,
    text: source.slice(from + 1, to),
  };
}

function parseLinkToken(source: string, from: number): InlineToken | null {
  if (source[from] !== "[" || source[from - 1] === "!") return null;

  const labelTo = source.indexOf("]", from + 1);
  if (labelTo <= from + 1 || source[labelTo + 1] !== "(") return null;

  const hrefTo = findClosingParen(source, labelTo + 2);
  if (hrefTo <= labelTo + 2) return null;

  return {
    kind: "link",
    from,
    to: hrefTo + 1,
    label: source.slice(from + 1, labelTo),
    href: source.slice(labelTo + 2, hrefTo).trim(),
  };
}

function parseDelimitedToken(
  source: string,
  from: number,
  delimiter: string,
  kind: "del" | "strong",
): InlineToken | null {
  if (!source.startsWith(delimiter, from)) return null;

  const contentFrom = from + delimiter.length;
  const closingFrom = source.indexOf(delimiter, contentFrom);
  if (closingFrom <= contentFrom) return null;

  const text = source.slice(contentFrom, closingFrom);
  if (!text.trim()) return null;

  return {
    kind,
    from,
    to: closingFrom + delimiter.length,
    text,
  };
}

function parseEmphasisToken(source: string, from: number, delimiter: "*" | "_"): InlineToken | null {
  if (source[from] !== delimiter) return null;
  if (source[from + 1] === delimiter || source[from - 1] === delimiter) return null;

  const contentFrom = from + 1;
  let closingFrom = source.indexOf(delimiter, contentFrom);
  while (closingFrom > contentFrom && source[closingFrom + 1] === delimiter) {
    closingFrom = source.indexOf(delimiter, closingFrom + 2);
  }

  if (closingFrom <= contentFrom) return null;

  const text = source.slice(contentFrom, closingFrom);
  if (!text.trim()) return null;

  return {
    kind: "em",
    from,
    to: closingFrom + 1,
    text,
  };
}

function findClosingParen(source: string, start: number): number {
  let escaped = false;
  for (let index = start; index < source.length; index += 1) {
    const character = source[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === "\\") {
      escaped = true;
      continue;
    }
    if (character === ")") return index;
  }
  return -1;
}

function isEscaped(source: string, index: number): boolean {
  let slashCount = 0;
  for (let cursor = index - 1; cursor >= 0 && source[cursor] === "\\"; cursor -= 1) {
    slashCount += 1;
  }
  return slashCount % 2 === 1;
}

function mightContainHtml(source: string): boolean {
  return /<\/?[a-z][\w:-]*(?:\s+[^<>]*)?>|<br\s*\/?>/i.test(source);
}
