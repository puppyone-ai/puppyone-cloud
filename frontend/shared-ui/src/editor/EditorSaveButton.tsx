"use client";

import { useEffect, useState, type ReactNode } from "react";

export type SaveStatus = "clean" | "dirty" | "saving" | "saved" | "error";

export type EditorSaveButtonProps = {
  status: SaveStatus;
  onSave: () => void;
};

export function EditorSaveButton({ status, onSave }: EditorSaveButtonProps) {
  const [shortcutHint, setShortcutHint] = useState("Ctrl+S");

  useEffect(() => {
    if (typeof navigator === "undefined") return;
    setShortcutHint(/Mac/i.test(navigator.platform) ? "Cmd+S" : "Ctrl+S");
  }, []);

  if (status === "clean") return null;

  if (status === "saving") {
    return (
      <ChipPill tone="muted">
        <span className="editor-save-pulse" aria-hidden />
        <span>Saving...</span>
      </ChipPill>
    );
  }

  if (status === "saved") {
    return (
      <ChipPill tone="success">
        <CheckIcon />
        <span>Saved</span>
      </ChipPill>
    );
  }

  if (status === "error") {
    return (
      <ChipButton tone="error" onClick={onSave} title={`Retry save (${shortcutHint})`}>
        <AlertIcon />
        <span>Save failed</span>
      </ChipButton>
    );
  }

  return (
    <ChipButton tone="action" onClick={onSave} title={`Save changes (${shortcutHint})`}>
      <SaveDiskIcon />
      <span>Save changes</span>
    </ChipButton>
  );
}

function ChipPill({
  tone,
  children,
}: {
  tone: "muted" | "success";
  children: ReactNode;
}) {
  return <span className={`editor-save-chip ${tone}`}>{children}</span>;
}

function ChipButton({
  tone,
  onClick,
  title,
  children,
}: {
  tone: "action" | "error";
  onClick: () => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <button
      className={`editor-save-chip ${tone}`}
      type="button"
      title={title}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function SaveDiskIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
      <polyline points="17 21 17 13 7 13 7 21" />
      <polyline points="7 3 7 8 15 8" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function AlertIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}
