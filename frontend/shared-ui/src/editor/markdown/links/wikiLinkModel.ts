export type MarkdownWikiLinkToken = {
  from: number;
  to: number;
  openingFrom: number;
  openingTo: number;
  targetFrom: number;
  targetTo: number;
  aliasFrom: number | null;
  aliasTo: number | null;
  closingFrom: number;
  closingTo: number;
  target: string;
  targetPath: string;
  heading: string | null;
  label: string;
};

export type MarkdownWikiLinkTargetParts = {
  targetPath: string;
  heading: string | null;
};

export function findWikiLinkTokens(source: string): MarkdownWikiLinkToken[] {
  const tokens: MarkdownWikiLinkToken[] = [];

  for (let index = 0; index < source.length;) {
    if (!source.startsWith("[[", index) || isEscaped(source, index)) {
      index += 1;
      continue;
    }

    const closingFrom = source.indexOf("]]", index + 2);
    if (closingFrom === -1) break;

    const contentFrom = index + 2;
    const content = source.slice(contentFrom, closingFrom);
    if (content.includes("\n")) {
      index += 2;
      continue;
    }

    const token = createWikiLinkToken(source, index, contentFrom, closingFrom);
    if (token) tokens.push(token);
    index = closingFrom + 2;
  }

  return tokens;
}

export function splitWikiLinkTarget(target: string): MarkdownWikiLinkTargetParts {
  const normalizedTarget = target.trim();
  const headingIndex = normalizedTarget.indexOf("#");

  if (headingIndex === -1) {
    return {
      targetPath: normalizedTarget,
      heading: null,
    };
  }

  return {
    targetPath: normalizedTarget.slice(0, headingIndex).trim(),
    heading: normalizedTarget.slice(headingIndex + 1).trim() || null,
  };
}

function createWikiLinkToken(
  source: string,
  openingFrom: number,
  contentFrom: number,
  closingFrom: number,
): MarkdownWikiLinkToken | null {
  const content = source.slice(contentFrom, closingFrom);
  if (!content.trim()) return null;

  const pipeOffset = findUnescapedPipe(content);
  const rawTargetRange = trimRange(content, 0, pipeOffset === -1 ? content.length : pipeOffset);
  if (rawTargetRange.from >= rawTargetRange.to) return null;

  const target = content.slice(rawTargetRange.from, rawTargetRange.to);
  const targetParts = splitWikiLinkTarget(target);
  const rawAliasRange = pipeOffset === -1
    ? null
    : trimRange(content, pipeOffset + 1, content.length);
  const hasAlias = Boolean(rawAliasRange && rawAliasRange.from < rawAliasRange.to);
  const alias = hasAlias && rawAliasRange
    ? content.slice(rawAliasRange.from, rawAliasRange.to)
    : null;

  return {
    from: openingFrom,
    to: closingFrom + 2,
    openingFrom,
    openingTo: contentFrom,
    targetFrom: contentFrom + rawTargetRange.from,
    targetTo: contentFrom + rawTargetRange.to,
    aliasFrom: hasAlias && rawAliasRange ? contentFrom + rawAliasRange.from : null,
    aliasTo: hasAlias && rawAliasRange ? contentFrom + rawAliasRange.to : null,
    closingFrom,
    closingTo: closingFrom + 2,
    target,
    targetPath: targetParts.targetPath,
    heading: targetParts.heading,
    label: alias ?? getDefaultWikiLinkLabel(targetParts, target),
  };
}

function getDefaultWikiLinkLabel(parts: MarkdownWikiLinkTargetParts, target: string): string {
  if (!parts.targetPath && parts.heading) return parts.heading;
  return target;
}

function findUnescapedPipe(content: string): number {
  for (let index = 0; index < content.length; index += 1) {
    if (content[index] === "|" && !isEscaped(content, index)) return index;
  }
  return -1;
}

function trimRange(source: string, from: number, to: number): { from: number; to: number } {
  let start = from;
  let end = to;

  while (start < end && /\s/.test(source[start])) start += 1;
  while (end > start && /\s/.test(source[end - 1])) end -= 1;

  return { from: start, to: end };
}

function isEscaped(source: string, index: number): boolean {
  let slashCount = 0;
  for (let cursor = index - 1; cursor >= 0 && source[cursor] === "\\"; cursor -= 1) {
    slashCount += 1;
  }
  return slashCount % 2 === 1;
}
