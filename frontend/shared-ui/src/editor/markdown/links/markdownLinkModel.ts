export type MarkdownLinkToken = {
  from: number;
  to: number;
  labelFrom: number;
  labelTo: number;
  hrefFrom: number;
  hrefTo: number;
  label: string;
  href: string;
};

export function findMarkdownLinkTokens(source: string): MarkdownLinkToken[] {
  const tokens: MarkdownLinkToken[] = [];

  for (let index = 0; index < source.length; index += 1) {
    if (source[index] !== "[" || source[index - 1] === "!" || isEscaped(source, index)) continue;

    const labelTo = findClosingBracket(source, index + 1);
    if (labelTo <= index + 1 || source[labelTo + 1] !== "(") continue;

    const hrefFrom = labelTo + 2;
    const hrefTo = findClosingParen(source, hrefFrom);
    if (hrefTo <= hrefFrom) continue;

    const rawLabel = source.slice(index + 1, labelTo);
    const rawHref = source.slice(hrefFrom, hrefTo);
    if (!rawLabel.trim() || !rawHref.trim()) continue;

    tokens.push({
      from: index,
      to: hrefTo + 1,
      labelFrom: index + 1,
      labelTo,
      hrefFrom,
      hrefTo,
      label: rawLabel,
      href: rawHref.trim(),
    });
    index = hrefTo;
  }

  return tokens;
}

export function isExternalMarkdownHref(href: string): boolean {
  const value = href.trim();
  return /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(value);
}

function findClosingBracket(source: string, start: number): number {
  for (let index = start; index < source.length; index += 1) {
    const character = source[index];
    if (character === "\n") return -1;
    if (character === "]" && !isEscaped(source, index)) return index;
  }
  return -1;
}

function findClosingParen(source: string, start: number): number {
  let escaped = false;
  for (let index = start; index < source.length; index += 1) {
    const character = source[index];
    if (character === "\n") return -1;
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
