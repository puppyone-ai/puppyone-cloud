export type HtmlSanitizerMode = "inline" | "block";

const INLINE_TAGS = [
  "a",
  "b",
  "br",
  "code",
  "del",
  "em",
  "i",
  "mark",
  "s",
  "span",
  "strong",
  "sub",
  "sup",
  "u",
] as const;

const BLOCK_TAGS = [
  ...INLINE_TAGS,
  "article",
  "blockquote",
  "caption",
  "dd",
  "details",
  "div",
  "dl",
  "dt",
  "figcaption",
  "figure",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "li",
  "ol",
  "p",
  "pre",
  "section",
  "summary",
  "table",
  "tbody",
  "td",
  "tfoot",
  "th",
  "thead",
  "tr",
  "ul",
] as const;

const BLOCKED_TAGS = [
  "base",
  "embed",
  "form",
  "iframe",
  "input",
  "link",
  "meta",
  "object",
  "script",
  "style",
  "template",
] as const;

const STYLE_PROPERTIES = [
  "background",
  "background-color",
  "border",
  "border-bottom",
  "border-bottom-color",
  "border-bottom-style",
  "border-bottom-width",
  "border-color",
  "border-left",
  "border-left-color",
  "border-left-style",
  "border-left-width",
  "border-radius",
  "border-right",
  "border-right-color",
  "border-right-style",
  "border-right-width",
  "border-style",
  "border-top",
  "border-top-color",
  "border-top-style",
  "border-top-width",
  "border-width",
  "color",
  "cursor",
  "display",
  "flex-wrap",
  "font-size",
  "font-style",
  "font-weight",
  "gap",
  "height",
  "line-height",
  "margin",
  "margin-bottom",
  "margin-left",
  "margin-right",
  "margin-top",
  "max-width",
  "min-width",
  "overflow",
  "padding",
  "padding-bottom",
  "padding-left",
  "padding-right",
  "padding-top",
  "text-align",
  "text-decoration",
  "text-decoration-line",
  "width",
] as const;

export const MARKDOWN_HTML_POLICY = {
  tags: {
    inline: new Set<string>(INLINE_TAGS),
    block: new Set<string>(BLOCK_TAGS),
    blocked: new Set<string>(BLOCKED_TAGS),
    void: new Set<string>(["br", "hr"]),
  },
  attributes: {
    global: new Set<string>(["aria-label", "style", "title"]),
    byTag: new Map<string, Set<string>>([
      ["a", new Set(["aria-label", "href", "style", "title"])],
      ["details", new Set(["aria-label", "open", "style", "title"])],
      ["td", new Set(["aria-label", "colspan", "rowspan", "style", "title"])],
      ["th", new Set(["aria-label", "colspan", "rowspan", "style", "title"])],
    ]),
    boolean: new Map<string, Set<string>>([
      ["details", new Set(["open"])],
    ]),
    numeric: new Map<string, Set<string>>([
      ["td", new Set(["colspan", "rowspan"])],
      ["th", new Set(["colspan", "rowspan"])],
    ]),
  },
  styles: {
    properties: new Set<string>(STYLE_PROPERTIES),
  },
  urls: {
    protocols: new Set(["http:", "https:", "mailto:"]),
    relativePrefixes: ["#", "/", "./", "../"],
  },
} as const;

export function isAllowedHtmlTag(tagName: string, mode: HtmlSanitizerMode): boolean {
  const allowedTags = mode === "block" ? MARKDOWN_HTML_POLICY.tags.block : MARKDOWN_HTML_POLICY.tags.inline;
  return allowedTags.has(tagName);
}

export function isBlockedHtmlTag(tagName: string): boolean {
  return MARKDOWN_HTML_POLICY.tags.blocked.has(tagName);
}

export function isVoidHtmlTag(tagName: string): boolean {
  return MARKDOWN_HTML_POLICY.tags.void.has(tagName);
}

export function isAllowedHtmlAttribute(tagName: string, attributeName: string): boolean {
  return (
    MARKDOWN_HTML_POLICY.attributes.global.has(attributeName) ||
    MARKDOWN_HTML_POLICY.attributes.byTag.get(tagName)?.has(attributeName) === true
  );
}

export function isBooleanHtmlAttribute(tagName: string, attributeName: string): boolean {
  return MARKDOWN_HTML_POLICY.attributes.boolean.get(tagName)?.has(attributeName) === true;
}

export function isNumericHtmlAttribute(tagName: string, attributeName: string): boolean {
  return MARKDOWN_HTML_POLICY.attributes.numeric.get(tagName)?.has(attributeName) === true;
}

export function isAllowedStyleProperty(property: string): boolean {
  return MARKDOWN_HTML_POLICY.styles.properties.has(property);
}

export function isSafeHref(href: string): boolean {
  const value = href.trim();
  if (!value) return false;
  if (MARKDOWN_HTML_POLICY.urls.relativePrefixes.some((prefix) => value.startsWith(prefix))) return true;
  if (!/^[a-z][a-z0-9+.-]*:/i.test(value)) return true;

  try {
    const url = new URL(value, window.location.href);
    return MARKDOWN_HTML_POLICY.urls.protocols.has(url.protocol);
  } catch {
    return false;
  }
}

export function isSafeStyleValue(property: string, value: string): boolean {
  const normalized = value.trim();
  if (!normalized || /[;<>{}]|url\s*\(|expression\s*\(|@import/i.test(normalized)) return false;
  if (isSafeCssVariableValue(normalized)) return true;

  if (property === "color" || property.endsWith("-color")) {
    return isSafeColorValue(normalized);
  }

  if (property === "background") {
    return isSafeColorValue(normalized);
  }

  if (property === "font-weight") {
    return /^(normal|bold|bolder|lighter|[1-9]00)$/i.test(normalized);
  }

  if (property === "font-style") {
    return /^(normal|italic|oblique)$/i.test(normalized);
  }

  if (property === "text-decoration" || property === "text-decoration-line") {
    return /^(none|underline|line-through|underline line-through|line-through underline)$/i.test(normalized);
  }

  if (property === "display") {
    return /^(block|flex|grid|inline|inline-block|inline-flex|inline-grid|none)$/i.test(normalized);
  }

  if (property === "flex-wrap") {
    return /^(nowrap|wrap|wrap-reverse)$/i.test(normalized);
  }

  if (property === "overflow") {
    return /^(auto|hidden|scroll|visible)$/i.test(normalized);
  }

  if (property === "cursor") {
    return /^(default|pointer|text)$/i.test(normalized);
  }

  if (property === "text-align") {
    return /^(left|center|right|start|end)$/i.test(normalized);
  }

  if (property.startsWith("border")) {
    return normalized
      .split(/\s+/)
      .every((part) => isSafeLengthValue(part) || isSafeColorValue(part) || /^(none|solid|dashed|dotted)$/i.test(part));
  }

  if (property === "font-size" || property === "line-height") {
    return isSafeLengthValue(normalized) || /^(normal|small|medium|large|x-large|xx-large|smaller|larger)$/i.test(normalized);
  }

  if (
    property === "gap" ||
    property === "height" ||
    property === "margin" ||
    property.startsWith("margin-") ||
    property === "max-width" ||
    property === "min-width" ||
    property === "padding" ||
    property.startsWith("padding-") ||
    property === "width"
  ) {
    return normalized.split(/\s+/).every(isSafeLengthValue);
  }

  return false;
}

function isSafeColorValue(value: string): boolean {
  return (
    /^#[0-9a-f]{3,8}$/i.test(value) ||
    /^rgba?\(\s*[\d.]+%?\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?(?:\s*,\s*(?:[\d.]+|[\d.]+%))?\s*\)$/i.test(value) ||
    /^hsla?\(\s*[\d.]+(?:deg|rad|turn)?\s*,\s*[\d.]+%\s*,\s*[\d.]+%(?:\s*,\s*(?:[\d.]+|[\d.]+%))?\s*\)$/i.test(value) ||
    isSafeCssVariableValue(value) ||
    /^(currentcolor|transparent|black|blue|brown|cyan|gray|green|grey|lime|magenta|maroon|navy|olive|orange|pink|purple|red|teal|white|yellow)$/i.test(value)
  );
}

function isSafeLengthValue(value: string): boolean {
  return (
    /^0$/.test(value) ||
    /^-?\d*\.?\d+(?:px|em|rem|%|vh|vw|ch|lh)$/i.test(value) ||
    /^auto$/i.test(value) ||
    isSafeCssVariableValue(value)
  );
}

function isSafeCssVariableValue(value: string): boolean {
  const match = /^var\(\s*(--[a-z0-9_-]+)(?:\s*,\s*(.+))?\s*\)$/i.exec(value);
  if (!match) return false;

  const fallback = match[2]?.trim();
  if (!fallback) return true;
  if (/[;<>{}]|url\s*\(|expression\s*\(|@import/i.test(fallback)) return false;

  return (
    /^#[0-9a-f]{3,8}$/i.test(fallback) ||
    /^-?\d*\.?\d+(?:px|em|rem|%|vh|vw|ch|lh)$/i.test(fallback) ||
    /^(currentcolor|transparent|black|blue|brown|cyan|gray|green|grey|lime|magenta|maroon|navy|olive|orange|pink|purple|red|teal|white|yellow)$/i.test(fallback)
  );
}
