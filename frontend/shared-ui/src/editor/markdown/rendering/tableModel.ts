import type { EditorState } from "@codemirror/state";

export type MarkdownTableBlock = {
  from: number;
  to: number;
  nextLineNumber: number;
  rows: MarkdownTableRow[];
};

export type MarkdownTableCell = {
  text: string;
  from: number;
  to: number;
  editable: boolean;
};

export type MarkdownTableRow = {
  cells: MarkdownTableCell[];
  header: boolean;
  lineTo: number;
};

type MarkdownSourceLine = {
  from: number;
  text: string;
};

export function getMarkdownTableBlock(state: EditorState, lineNumber: number): MarkdownTableBlock | null {
  const doc = state.doc;
  if (lineNumber >= doc.lines) return null;

  const headerLine = doc.line(lineNumber);
  const delimiterLine = doc.line(lineNumber + 1);
  if (!isTableHeaderLine(headerLine.text) || !isTableDelimiterLine(delimiterLine.text)) return null;

  const rows: MarkdownTableRow[] = [{
    cells: splitTableCellsWithPositions(headerLine),
    header: true,
    lineTo: headerLine.to,
  }];
  let lastLine = delimiterLine;
  let nextLineNumber = lineNumber + 2;

  while (nextLineNumber <= doc.lines) {
    const rowLine = doc.line(nextLineNumber);
    if (!isMarkdownTableLine(rowLine.text) || isTableDelimiterLine(rowLine.text)) break;
    rows.push({
      cells: splitTableCellsWithPositions(rowLine),
      header: false,
      lineTo: rowLine.to,
    });
    lastLine = rowLine;
    nextLineNumber += 1;
  }

  if (rows.length < 2) return null;

  return {
    from: headerLine.from,
    to: lastLine.to,
    nextLineNumber,
    rows: normalizeTableRows(rows),
  };
}

export function isMarkdownTableLine(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed.includes("|")) return false;
  if (/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(trimmed)) return true;
  return /^\|.+\|$/.test(trimmed);
}

function isTableHeaderLine(text: string): boolean {
  return splitTableCells(text).length >= 2;
}

function isTableDelimiterLine(text: string): boolean {
  const cells = splitTableCells(text);
  return cells.length >= 2 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
}

function splitTableCells(text: string): string[] {
  const trimmed = text.trim();
  if (!trimmed.includes("|")) return [];
  const withoutEdgePipes = trimmed.replace(/^\|/, "").replace(/\|$/, "");
  return withoutEdgePipes.split("|").map((cell) => cell.trim());
}

function splitTableCellsWithPositions(line: MarkdownSourceLine): MarkdownTableCell[] {
  const text = line.text;
  const pipeIndexes = Array.from(text.matchAll(/\|/g), (match) => match.index).filter((index): index is number => index != null);
  if (pipeIndexes.length === 0) return [];

  const firstContentIndex = text.search(/\S/);
  const lastContentIndex = findLastNonWhitespaceIndex(text);
  const hasLeadingPipe = firstContentIndex >= 0 && text[firstContentIndex] === "|";
  const hasTrailingPipe = lastContentIndex >= 0 && text[lastContentIndex] === "|";
  const boundaries: number[] = [];

  if (hasLeadingPipe) {
    boundaries.push(pipeIndexes[0]);
    boundaries.push(...pipeIndexes.slice(1));
  } else {
    boundaries.push(-1, ...pipeIndexes);
  }

  if (!hasTrailingPipe) {
    boundaries.push(text.length);
  }

  const cells: MarkdownTableCell[] = [];
  for (let index = 0; index < boundaries.length - 1; index += 1) {
    const rawFrom = Math.max(0, boundaries[index] + 1);
    const rawTo = Math.min(text.length, boundaries[index + 1]);
    const raw = text.slice(rawFrom, rawTo);
    const leadingWhitespaceLength = raw.match(/^\s*/)?.[0].length ?? 0;
    const trailingWhitespaceLength = raw.match(/\s*$/)?.[0].length ?? 0;
    const cellFrom = line.from + rawFrom + leadingWhitespaceLength;
    const cellTo = line.from + rawTo - trailingWhitespaceLength;
    cells.push({
      text: raw.trim(),
      from: cellFrom,
      to: Math.max(cellFrom, cellTo),
      editable: true,
    });
  }

  return cells;
}

function findLastNonWhitespaceIndex(text: string): number {
  for (let index = text.length - 1; index >= 0; index -= 1) {
    if (!/\s/.test(text[index])) return index;
  }
  return -1;
}

function normalizeTableRows(rows: MarkdownTableRow[]): MarkdownTableRow[] {
  const width = Math.max(...rows.map((row) => row.cells.length));
  return rows.map((row) => ({
    ...row,
    cells: Array.from({ length: width }, (_, index) => row.cells[index] ?? {
      text: "",
      from: row.lineTo,
      to: row.lineTo,
      editable: false,
    }),
  }));
}
