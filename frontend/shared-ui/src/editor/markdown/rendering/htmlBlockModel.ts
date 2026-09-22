import type { EditorState } from "@codemirror/state";

const VOID_HTML_BLOCK_TAGS = new Set([
  "area",
  "base",
  "br",
  "col",
  "embed",
  "hr",
  "img",
  "input",
  "link",
  "meta",
  "source",
  "track",
  "wbr",
]);

export type MarkdownHtmlBlock = {
  from: number;
  to: number;
  nextLineNumber: number;
  source: string;
  tagName: string;
  closed: boolean;
};

type HtmlTagToken = {
  tagName: string;
  closing: boolean;
  selfClosing: boolean;
};

export function getMarkdownHtmlBlock(state: EditorState, lineNumber: number): MarkdownHtmlBlock | null {
  const doc = state.doc;
  const firstLine = doc.line(lineNumber);
  const start = getHtmlBlockStart(firstLine.text);
  if (!start) return null;

  const sourceLines: string[] = [];
  let balance = 0;
  let lastLine = firstLine;
  let nextLineNumber = lineNumber;
  let closed = false;

  while (nextLineNumber <= doc.lines) {
    const line = doc.line(nextLineNumber);
    sourceLines.push(line.text);
    lastLine = line;

    // Obsidian-style container HTML can include blank lines; keep scanning until the root tag closes.
    balance += getTagBalance(line.text, start.tagName);
    nextLineNumber += 1;

    if (VOID_HTML_BLOCK_TAGS.has(start.tagName) || balance <= 0) {
      closed = true;
      break;
    }
  }

  return {
    from: firstLine.from,
    to: lastLine.to,
    nextLineNumber,
    source: sourceLines.join("\n"),
    tagName: start.tagName,
    closed,
  };
}

function getHtmlBlockStart(text: string): { tagName: string } | null {
  const contentStart = text.search(/\S/);
  if (contentStart < 0 || text[contentStart] !== "<") return null;

  const token = readHtmlTagToken(text, contentStart);
  if (!token || token.closing) return null;
  return { tagName: token.tagName };
}

function getTagBalance(text: string, tagName: string): number {
  if (VOID_HTML_BLOCK_TAGS.has(tagName)) return 0;

  let balance = 0;
  const tokens = scanHtmlTagTokens(text);
  for (const token of tokens) {
    if (token.tagName !== tagName) continue;
    if (token.closing) {
      balance -= 1;
    } else if (!token.selfClosing) {
      balance += 1;
    }
  }

  return balance;
}

function scanHtmlTagTokens(text: string): HtmlTagToken[] {
  const tokens: HtmlTagToken[] = [];
  let cursor = 0;

  while (cursor < text.length) {
    const tagStart = text.indexOf("<", cursor);
    if (tagStart === -1) break;

    const token = readHtmlTagToken(text, tagStart);
    if (!token) {
      cursor = tagStart + 1;
      continue;
    }

    tokens.push(token);
    cursor = findHtmlTagEnd(text, tagStart) + 1;
  }

  return tokens;
}

function readHtmlTagToken(text: string, tagStart: number): HtmlTagToken | null {
  if (text[tagStart] !== "<") return null;
  const next = text[tagStart + 1];
  if (!next || next === "!" || next === "?") return null;

  let cursor = tagStart + 1;
  const closing = text[cursor] === "/";
  if (closing) cursor += 1;

  const tagNameMatch = /^[a-z][a-z0-9-]*/i.exec(text.slice(cursor));
  if (!tagNameMatch) return null;

  const tagName = tagNameMatch[0].toLowerCase();
  cursor += tagName.length;
  const afterName = text[cursor];
  if (afterName && !/[\s/>]/.test(afterName)) return null;

  const tagEnd = findHtmlTagEnd(text, tagStart);
  if (tagEnd === -1) return null;

  const rawTag = text.slice(tagStart, tagEnd + 1);
  return {
    tagName,
    closing,
    selfClosing: !closing && (VOID_HTML_BLOCK_TAGS.has(tagName) || /\/\s*>$/.test(rawTag)),
  };
}

function findHtmlTagEnd(text: string, tagStart: number): number {
  let quote: '"' | "'" | null = null;

  for (let index = tagStart + 1; index < text.length; index += 1) {
    const character = text[index];
    if (quote) {
      if (character === quote) quote = null;
      continue;
    }

    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }

    if (character === ">") return index;
  }

  return -1;
}
