export type DataNodeKind =
  | "folder"
  | "markdown"
  | "json"
  | "html"
  | "image"
  | "audio"
  | "pdf"
  | "video"
  | "spreadsheet"
  | "archive"
  | "document"
  | "binary"
  | "code"
  | "text"
  | "file";

export type DataNodeStatus = "clean" | "modified" | "created" | "deleted" | "moved";

export type DataSourceKind =
  | "local"
  | "cloud"
  | "github"
  | "notion"
  | "google_drive"
  | "connector";

export type Workspace = {
  id: string;
  name: string;
  path: string;
  status: "protected" | "recording" | "paused";
  commitCount?: number;
  cloudState?: "local" | "syncing" | "synced";
};

export type DataNode = {
  id: string;
  name: string;
  path: string;
  type: DataNodeKind;
  size?: string | null;
  modified?: string | null;
  status?: DataNodeStatus;
  preview?: string | null;
  content?: string | null;
  hasChildren?: boolean;
  children?: DataNode[] | null;
  source?: DataSourceKind;
};

export type FileContent = {
  path: string;
  name: string;
  type: DataNodeKind;
  content?: string | null;
  mimeType?: string | null;
  size?: string | null;
  url?: string | null;
};

export type DataCapabilities = {
  create?: boolean;
  rename?: boolean;
  delete?: boolean;
  move?: boolean;
  write?: boolean;
  history?: boolean;
  accessPoints?: boolean;
  cloudSync?: boolean;
  localGit?: boolean;
  connectors?: boolean;
};

export type DataPort = {
  listChildren: (folderPath: string | null) => Promise<DataNode[]>;
  readFile?: (path: string) => Promise<FileContent>;
  getFileUrl?: (path: string) => string | Promise<string>;
  writeFile?: (path: string, content: string) => Promise<void>;
  createFolder?: (path: string) => Promise<void>;
  createFile?: (path: string, content?: string) => Promise<void>;
  renameNode?: (path: string, nextName: string) => Promise<void>;
  deleteNode?: (path: string) => Promise<void>;
  moveNode?: (from: string, to: string) => Promise<void>;
};

export const defaultDataCapabilities: DataCapabilities = {
  create: false,
  rename: false,
  delete: false,
  move: false,
  write: false,
  history: false,
  accessPoints: false,
  cloudSync: false,
  localGit: false,
  connectors: false,
};
