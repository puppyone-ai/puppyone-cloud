import type { CSSProperties, ReactNode } from "react";

export type BreadcrumbSegment = {
  label: ReactNode;
  href?: string;
};

export type ProjectsHeaderProps = {
  pathSegments: BreadcrumbSegment[];
  onBack?: () => void;
  actionSlot?: ReactNode;
};

export function ProjectsHeader({ pathSegments, onBack, actionSlot }: ProjectsHeaderProps) {
  return (
    <header className="projects-header" style={headerStyle}>
      <div style={headerLeftStyle}>
        {onBack && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", paddingLeft: 8, paddingRight: 8 }}>
            <button
              onClick={onBack}
              style={backButtonStyle}
              onMouseEnter={(event) => {
                event.currentTarget.style.background = "var(--po-hover)";
                event.currentTarget.style.color = "var(--po-text)";
              }}
              onMouseLeave={(event) => {
                event.currentTarget.style.background = "transparent";
                event.currentTarget.style.color = "var(--po-text-subtle)";
              }}
              title="Back"
              type="button"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M19 12H5" />
                <path d="M12 19l-7-7 7-7" />
              </svg>
            </button>
          </div>
        )}

        <div style={{ display: "flex", alignItems: "center", minWidth: 0, overflow: "hidden" }}>
          {pathSegments.map((segment, index) => {
            const isLast = index === pathSegments.length - 1;
            return (
              <div key={index} style={{ display: "flex", alignItems: "center", minWidth: 0 }}>
                {index > 0 && <span style={{ margin: "0 8px", color: "var(--po-text-disabled)" }}>/</span>}
                <span
                  style={{
                    ...pathStyle,
                    color: isLast ? "var(--po-text)" : "var(--po-text-muted)",
                  }}
                  title={typeof segment.label === "string" ? segment.label : undefined}
                >
                  {segment.label}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {actionSlot && <div style={headerActionStyle}>{actionSlot}</div>}
    </header>
  );
}

const headerStyle: CSSProperties = {
  height: 46,
  paddingLeft: 16,
  paddingRight: 16,
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  borderBottom: "1px solid var(--po-divider)",
  background: "var(--po-header)",
  position: "relative",
  zIndex: 20,
  overflow: "visible",
};

const headerLeftStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  minWidth: 0,
  overflow: "hidden",
};

const headerActionStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "flex-end",
  flexShrink: 0,
  marginLeft: 16,
  position: "relative",
  zIndex: 21,
};

const backButtonStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: 30,
  height: 30,
  background: "transparent",
  border: "none",
  borderRadius: 6,
  cursor: "default",
  color: "var(--po-text-subtle)",
  transition: "all 0.15s",
};

const pathStyle: CSSProperties = {
  display: "block",
  overflow: "hidden",
  maxWidth: 260,
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: 13,
  fontWeight: 600,
  lineHeight: "18px",
};
