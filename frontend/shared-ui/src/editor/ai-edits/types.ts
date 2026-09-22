export type AiEditHunkState = "pending" | "accepted" | "rejected" | "stale" | "conflicted";

export type AiEditFileStatus = "modified" | "created" | "deleted" | "renamed";

export type AiEditHunkKind = "added" | "removed" | "modified";

export type AiEditLineRange = {
  startLine: number;
  lineCount: number;
};

export type AiEditHunk = {
  id: string;
  kind: AiEditHunkKind;
  state: AiEditHunkState;
  oldRange: AiEditLineRange;
  newRange: AiEditLineRange;
  oldText: string;
  newText: string;
};

export type AiEditFile = {
  id: string;
  requestId: string;
  path: string;
  oldPath?: string | null;
  status: AiEditFileStatus;
  beforeHash: string;
  afterHash: string;
  additions: number;
  deletions: number;
  hunks: AiEditHunk[];
};

export type AiEditRequest = {
  id: string;
  sessionId: string;
  agentName?: string;
  title?: string;
  createdAt?: string;
  files: AiEditFile[];
};

