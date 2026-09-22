export interface ConflictBlock {
  startLine: number;
  endLine: number;
  ours: string;
  theirs: string;
  oursLabel: string;
  theirsLabel: string;
}

const OPEN = /^<<<<<<<\s*(.*)$/;
const SEPARATOR = /^=======\s*$/;
const CLOSE = /^>>>>>>>\s*(.*)$/;

export function parseConflictMarkers(content: string): ConflictBlock[] {
  if (!content || !content.includes("<<<<<<<")) return [];

  const lines = content.split("\n");
  const blocks: ConflictBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const openMatch = OPEN.exec(lines[index]);
    if (!openMatch) {
      index += 1;
      continue;
    }

    const startLine = index;
    const oursLabel = openMatch[1].trim();
    let separator = -1;
    let endLine = -1;
    let theirsLabel = "";

    for (let cursor = index + 1; cursor < lines.length; cursor += 1) {
      if (separator === -1 && SEPARATOR.test(lines[cursor])) {
        separator = cursor;
        continue;
      }

      const closeMatch = CLOSE.exec(lines[cursor]);
      if (separator !== -1 && closeMatch) {
        endLine = cursor;
        theirsLabel = closeMatch[1].trim();
        break;
      }
    }

    if (separator === -1 || endLine === -1) {
      index += 1;
      continue;
    }

    blocks.push({
      startLine,
      endLine,
      ours: lines.slice(startLine + 1, separator).join("\n"),
      theirs: lines.slice(separator + 1, endLine).join("\n"),
      oursLabel,
      theirsLabel,
    });
    index = endLine + 1;
  }

  return blocks;
}

export function resolveConflictMarkers(
  content: string,
  pick: "ours" | "theirs" | ((block: ConflictBlock) => string),
): string {
  const blocks = parseConflictMarkers(content);
  if (blocks.length === 0) return content;

  const lines = content.split("\n");
  const sorted = [...blocks].sort((a, b) => b.startLine - a.startLine);

  for (const block of sorted) {
    const replacement = typeof pick === "function"
      ? pick(block)
      : pick === "ours"
        ? block.ours
        : block.theirs;
    const replacementLines = replacement.split("\n");
    if (replacementLines[replacementLines.length - 1] === "") replacementLines.pop();
    lines.splice(block.startLine, block.endLine - block.startLine + 1, ...replacementLines);
  }

  return lines.join("\n");
}

export function hasConflictMarkers(content: string | null | undefined): boolean {
  if (!content?.includes("<<<<<<<")) return false;
  return parseConflictMarkers(content).length > 0;
}
