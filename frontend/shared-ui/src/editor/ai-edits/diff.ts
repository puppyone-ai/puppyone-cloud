import type { AiEditFile, AiEditFileStatus, AiEditHunk, AiEditHunkKind, AiEditRequest } from "./types";

export type CreateAiEditFileInput = {
  requestId: string;
  path: string;
  oldPath?: string | null;
  before: string;
  after: string;
  status?: AiEditFileStatus;
};

export type CreateAiEditRequestInput = {
  id: string;
  sessionId: string;
  agentName?: string;
  title?: string;
  createdAt?: string;
  files: CreateAiEditFileInput[];
};

type LineOp =
  | { kind: "equal"; text: string }
  | { kind: "add"; text: string }
  | { kind: "remove"; text: string };

const MAX_EXACT_DIFF_LINES = 2200;

export function createAiEditRequest(input: CreateAiEditRequestInput): AiEditRequest {
  return {
    id: input.id,
    sessionId: input.sessionId,
    agentName: input.agentName,
    title: input.title,
    createdAt: input.createdAt,
    files: input.files
      .map((file) => createAiEditFile(file))
      .filter((file) => file.hunks.length > 0 || file.status !== "modified"),
  };
}

export function createAiEditFile(input: CreateAiEditFileInput): AiEditFile {
  const beforeLines = splitLines(input.before);
  const afterLines = splitLines(input.after);
  const status = input.status ?? inferFileStatus(input.before, input.after);
  const hunks = buildHunks(input.requestId, input.path, beforeLines, afterLines);

  return {
    id: `${input.requestId}:${input.path}`,
    requestId: input.requestId,
    path: input.path,
    oldPath: input.oldPath ?? null,
    status,
    beforeHash: stableContentHash(input.before),
    afterHash: stableContentHash(input.after),
    additions: hunks.reduce((sum, hunk) => sum + hunk.newRange.lineCount, 0),
    deletions: hunks.reduce((sum, hunk) => sum + hunk.oldRange.lineCount, 0),
    hunks,
  };
}

export function getAiEditFileForPath(request: AiEditRequest | null | undefined, path: string | null | undefined): AiEditFile | undefined {
  if (!request || !path) return undefined;
  return request.files.find((file) => file.path === path || file.oldPath === path);
}

export function getAiEditTotals(request: AiEditRequest | null | undefined) {
  const files = request?.files ?? [];
  return files.reduce(
    (totals, file) => ({
      files: totals.files + 1,
      additions: totals.additions + file.additions,
      deletions: totals.deletions + file.deletions,
    }),
    { files: 0, additions: 0, deletions: 0 },
  );
}

function buildHunks(requestId: string, path: string, beforeLines: string[], afterLines: string[]): AiEditHunk[] {
  if (beforeLines.length + afterLines.length > MAX_EXACT_DIFF_LINES) {
    return [createHunk(requestId, path, 0, 1, beforeLines, 1, afterLines)];
  }

  const ops = buildLineOps(beforeLines, afterLines);
  const hunks: AiEditHunk[] = [];
  let oldLine = 1;
  let newLine = 1;
  let oldStart = 1;
  let newStart = 1;
  let removed: string[] = [];
  let added: string[] = [];

  const flush = () => {
    if (removed.length === 0 && added.length === 0) return;
    hunks.push(createHunk(requestId, path, hunks.length, oldStart, removed, newStart, added));
    removed = [];
    added = [];
  };

  for (const op of ops) {
    if (op.kind === "equal") {
      flush();
      oldLine += 1;
      newLine += 1;
      oldStart = oldLine;
      newStart = newLine;
    } else {
      if (removed.length === 0 && added.length === 0) {
        oldStart = oldLine;
        newStart = newLine;
      }

      if (op.kind === "remove") {
        removed.push(op.text);
        oldLine += 1;
      } else {
        added.push(op.text);
        newLine += 1;
      }
    }
  }

  flush();
  return hunks;
}

function createHunk(
  requestId: string,
  path: string,
  index: number,
  oldStart: number,
  oldLines: string[],
  newStart: number,
  newLines: string[],
): AiEditHunk {
  const kind: AiEditHunkKind = oldLines.length === 0
    ? "added"
    : newLines.length === 0
      ? "removed"
      : "modified";

  return {
    id: `${requestId}:${path}:h${index + 1}`,
    kind,
    state: "pending",
    oldRange: {
      startLine: oldStart,
      lineCount: oldLines.length,
    },
    newRange: {
      startLine: newStart,
      lineCount: newLines.length,
    },
    oldText: oldLines.join("\n"),
    newText: newLines.join("\n"),
  };
}

function buildLineOps(beforeLines: string[], afterLines: string[]): LineOp[] {
  const table = buildLcsTable(beforeLines, afterLines);
  const ops: LineOp[] = [];
  let i = 0;
  let j = 0;

  while (i < beforeLines.length && j < afterLines.length) {
    if (beforeLines[i] === afterLines[j]) {
      ops.push({ kind: "equal", text: beforeLines[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      ops.push({ kind: "remove", text: beforeLines[i] });
      i += 1;
    } else {
      ops.push({ kind: "add", text: afterLines[j] });
      j += 1;
    }
  }

  while (i < beforeLines.length) {
    ops.push({ kind: "remove", text: beforeLines[i] });
    i += 1;
  }
  while (j < afterLines.length) {
    ops.push({ kind: "add", text: afterLines[j] });
    j += 1;
  }

  return ops;
}

function buildLcsTable(left: string[], right: string[]): number[][] {
  const rows = left.length + 1;
  const columns = right.length + 1;
  const table = Array.from({ length: rows }, () => Array<number>(columns).fill(0));

  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      table[i][j] = left[i] === right[j]
        ? table[i + 1][j + 1] + 1
        : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }

  return table;
}

function splitLines(value: string): string[] {
  if (!value) return [];
  return value.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
}

function inferFileStatus(before: string, after: string): AiEditFileStatus {
  if (!before && after) return "created";
  if (before && !after) return "deleted";
  return "modified";
}

function stableContentHash(value: string): string {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

