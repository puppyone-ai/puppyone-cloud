import {
  isAllowedHtmlAttribute,
  isAllowedHtmlTag,
  isAllowedStyleProperty,
  isBlockedHtmlTag,
  isBooleanHtmlAttribute,
  isNumericHtmlAttribute,
  isSafeHref,
  isSafeStyleValue,
  isVoidHtmlTag,
  type HtmlSanitizerMode,
} from "./markdownHtmlPolicy";

export type MarkdownTextRenderer = (target: Node, text: string) => void;

export type SanitizedHtmlResult = {
  fragment: DocumentFragment;
  supported: boolean;
  reasons: string[];
};

type SanitizeContext = {
  mode: HtmlSanitizerMode;
  strict: boolean;
  reasons: string[];
  renderText?: MarkdownTextRenderer;
};

export function appendSanitizedInlineHtml(
  target: Node,
  source: DocumentFragment | HTMLElement,
  renderText: MarkdownTextRenderer,
) {
  const context: SanitizeContext = {
    mode: "inline",
    strict: false,
    reasons: [],
    renderText,
  };

  appendSanitizedChildren(target, source, context);
}

export function createSanitizedBlockHtmlFragment(source: string): SanitizedHtmlResult {
  const template = document.createElement("template");
  template.innerHTML = source;

  const context: SanitizeContext = {
    mode: "block",
    strict: true,
    reasons: [],
  };
  const fragment = document.createDocumentFragment();
  appendSanitizedChildren(fragment, template.content, context);

  return {
    fragment,
    supported: context.reasons.length === 0,
    reasons: Array.from(new Set(context.reasons)),
  };
}

function appendSanitizedChildren(target: Node, source: DocumentFragment | HTMLElement, context: SanitizeContext) {
  for (const child of Array.from(source.childNodes)) {
    appendSanitizedNode(target, child, context);
  }
}

function appendSanitizedNode(target: Node, node: ChildNode, context: SanitizeContext) {
  if (node.nodeType === Node.TEXT_NODE) {
    const text = node.textContent ?? "";
    if (context.renderText) context.renderText(target, text);
    else target.appendChild(document.createTextNode(text));
    return;
  }

  if (!(node instanceof HTMLElement)) return;

  const tagName = node.tagName.toLowerCase();
  if (isBlockedHtmlTag(tagName)) {
    markUnsupported(context, `<${tagName}> is not supported`);
    return;
  }

  if (!isAllowedHtmlTag(tagName, context.mode)) {
    markUnsupported(context, `<${tagName}> is not supported`);
    if (!context.strict) appendSanitizedChildren(target, node, context);
    return;
  }

  const element = document.createElement(tagName);
  copySafeAttributes(element, node, tagName, context);

  if (isVoidHtmlTag(tagName)) {
    target.appendChild(element);
    return;
  }

  appendSanitizedChildren(element, node, context);
  target.appendChild(element);
}

function copySafeAttributes(target: HTMLElement, source: HTMLElement, tagName: string, context: SanitizeContext) {
  for (const attribute of Array.from(source.attributes)) {
    const name = attribute.name.toLowerCase();
    const value = attribute.value;

    if (name.startsWith("on")) {
      markUnsupported(context, `event handler "${name}" is not supported`);
      continue;
    }

    if (!isAllowedHtmlAttribute(tagName, name)) {
      markUnsupported(context, `attribute "${name}" is not supported`);
      continue;
    }

    if (name === "style") {
      copySafeInlineStyles(target, source, context);
      continue;
    }

    if (tagName === "a" && name === "href") {
      if (isSafeHref(value)) {
        target.setAttribute("href", value.trim());
        target.setAttribute("rel", "noreferrer noopener");
        target.setAttribute("target", "_blank");
      } else {
        markUnsupported(context, "unsafe link URL is not supported");
      }
      continue;
    }

    if (isBooleanHtmlAttribute(tagName, name)) {
      target.setAttribute(name, "");
      continue;
    }

    if (isNumericHtmlAttribute(tagName, name)) {
      if (/^[1-9]\d?$/.test(value.trim())) target.setAttribute(name, value.trim());
      else markUnsupported(context, `${name} value is not supported`);
      continue;
    }

    if (name === "title" || name === "aria-label") {
      target.setAttribute(name, value);
      continue;
    }

    markUnsupported(context, `attribute "${name}" is not supported`);
  }
}

function copySafeInlineStyles(target: HTMLElement, source: HTMLElement, context: SanitizeContext) {
  for (const property of Array.from(source.style)) {
    const name = property.toLowerCase();
    const value = source.style.getPropertyValue(property);
    if (!isAllowedStyleProperty(name)) {
      markUnsupported(context, `style "${name}" is not supported`);
      continue;
    }
    if (!isSafeStyleValue(name, value)) {
      markUnsupported(context, `style value for "${name}" is not supported`);
      continue;
    }
    target.style.setProperty(name, value.trim());
  }
}

function markUnsupported(context: SanitizeContext, reason: string) {
  context.reasons.push(reason);
}
