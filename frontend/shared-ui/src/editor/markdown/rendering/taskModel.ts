export type MarkdownTaskLine = {
  from: number;
  to: number;
  depth: number;
  checked: boolean;
  prefixFrom: number;
  prefixTo: number;
  checkboxFrom: number;
  checkboxTo: number;
  content: string;
  contentFrom: number;
  contentTo: number;
};

type MarkdownSourceLine = {
  from: number;
  to: number;
  text: string;
};

export function getMarkdownTaskLine(line: MarkdownSourceLine): MarkdownTaskLine | null {
  const match = /^(\s*)([-*+]|\d+[.)])\s+(\[[ xX]\])\s?/.exec(line.text);
  if (!match) return null;

  const checkboxFrom = line.from + match[1].length + match[2].length + 1;
  const checkboxTo = checkboxFrom + match[3].length;
  const contentFrom = line.from + match[0].length;

  return {
    from: line.from,
    to: line.to,
    depth: getListDepth(match[1]),
    checked: match[3].toLowerCase() === "[x]",
    prefixFrom: line.from,
    prefixTo: contentFrom,
    checkboxFrom,
    checkboxTo,
    content: line.text.slice(match[0].length),
    contentFrom,
    contentTo: line.to,
  };
}

function getListDepth(leadingWhitespace: string): number {
  return Math.floor(leadingWhitespace.replace(/\t/g, "    ").length / 2);
}
