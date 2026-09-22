import type { ReactNode } from "react";

export type EmptyStateProps = {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
};

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="po-empty-state">
      {icon && <div className="po-empty-state__icon">{icon}</div>}
      <strong>{title}</strong>
      {description && <span>{description}</span>}
      {action && <div className="po-empty-state__action">{action}</div>}
    </div>
  );
}

