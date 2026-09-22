import type { DataNode } from "../core/types";

export const sharedUiTreeFixture: DataNode[] = [
  {
    id: "docs",
    name: "docs",
    path: "docs",
    type: "folder",
    children: [
      {
        id: "docs/overview.md",
        name: "overview.md",
        path: "docs/overview.md",
        type: "markdown",
        preview: "# Overview\nShared UI fixture.",
      },
      {
        id: "docs/config.json",
        name: "config.json",
        path: "docs/config.json",
        type: "json",
        preview: "{\"enabled\": true}",
      },
    ],
  },
  {
    id: "notes.txt",
    name: "notes.txt",
    path: "notes.txt",
    type: "file",
    preview: "Plain text fixture.",
  },
];

