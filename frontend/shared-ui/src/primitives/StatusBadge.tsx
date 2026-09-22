import type { ReactNode } from "react";

export type StatusBadgeTone = "neutral" | "success" | "warning" | "danger" | "info";

export type StatusBadgeProps = {
  tone?: StatusBadgeTone;
  children: ReactNode;
};

export function StatusBadge({ tone = "neutral", children }: StatusBadgeProps) {
  return <span className={`po-status-badge po-status-badge--${tone}`}>{children}</span>;
}

