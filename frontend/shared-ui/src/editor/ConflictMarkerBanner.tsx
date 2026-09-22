"use client";

import { useMemo, useState } from "react";
import {
  parseConflictMarkers,
  resolveConflictMarkers,
  type ConflictBlock,
} from "./conflictMarkers";

export type ConflictMarkerBannerProps = {
  content: string;
  onResolve?: (newContent: string) => void;
};

export function ConflictMarkerBanner({
  content,
  onResolve,
}: ConflictMarkerBannerProps) {
  const blocks = useMemo<ConflictBlock[]>(() => parseConflictMarkers(content), [content]);
  const [expanded, setExpanded] = useState(false);

  if (blocks.length === 0) return null;

  const pickAll = (side: "ours" | "theirs") => {
    if (!onResolve) return;
    onResolve(resolveConflictMarkers(content, side));
  };

  const pickOne = (idx: number, side: "ours" | "theirs") => {
    if (!onResolve) return;
    onResolve(
      resolveConflictMarkers(content, (block) => {
        if (block !== blocks[idx]) return block.ours;
        return side === "ours" ? block.ours : block.theirs;
      }),
    );
  };

  return (
    <div className="conflict-marker-banner">
      <div className="conflict-marker-banner-row">
        <span className="conflict-marker-badge">conflict</span>
        <span className="conflict-marker-copy">
          This file has <strong>{blocks.length}</strong> unresolved merge
          conflict{blocks.length > 1 ? "s" : ""}.
        </span>
        {onResolve && (
          <div className="conflict-marker-actions">
            <button type="button" onClick={() => pickAll("ours")}>
              Keep server
            </button>
            <button type="button" onClick={() => pickAll("theirs")}>
              Keep incoming
            </button>
          </div>
        )}
        <button type="button" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Hide" : "Show"} blocks
        </button>
      </div>

      {expanded && (
        <div className="conflict-marker-blocks">
          {blocks.map((block, index) => (
            <div className="conflict-marker-block" key={`${block.startLine}:${block.endLine}`}>
              <div className="conflict-marker-block-title">
                block {index + 1} of {blocks.length}, line {block.startLine + 1}
              </div>
              <div className="conflict-marker-sides">
                <SideColumn
                  label={block.oursLabel || "current"}
                  content={block.ours}
                  onPick={onResolve ? () => pickOne(index, "ours") : undefined}
                />
                <SideColumn
                  label={block.theirsLabel || "incoming"}
                  content={block.theirs}
                  onPick={onResolve ? () => pickOne(index, "theirs") : undefined}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SideColumn({
  label,
  content,
  onPick,
}: {
  label: string;
  content: string;
  onPick?: () => void;
}) {
  return (
    <div className="conflict-marker-side">
      <div className="conflict-marker-side-title">
        <span>{label}</span>
        {onPick && (
          <button type="button" onClick={onPick}>
            Pick
          </button>
        )}
      </div>
      <pre>{content || " "}</pre>
    </div>
  );
}
