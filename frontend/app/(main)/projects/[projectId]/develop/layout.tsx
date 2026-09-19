'use client';

import React, { use } from 'react';
import { WorkspaceLink as Link } from '@/features/workspace/navigation';
import { usePathname } from 'next/navigation';
import { CHROME_LABEL_TYPOGRAPHY } from '@/lib/uiTypography';

const SIDEBAR_WIDTH = 200;

const T = {
  bg: 'var(--po-canvas)',
  rail: 'var(--po-sidebar)',
  border: 'var(--po-border)',
  text1: 'var(--po-text)',
  text2: 'var(--po-text-muted)',
  text3: 'var(--po-text-disabled)',
  fontSans: 'var(--po-font-sans)',
} as const;

interface DevelopLayoutProps {
  children: React.ReactNode;
  params: Promise<{ projectId: string }>;
}

export default function DevelopLayout({
  children,
  params,
}: DevelopLayoutProps) {
  const { projectId } = use(params);
  const pathname = usePathname();
  const logsHref = `/projects/${projectId}/develop/logs`;
  const logsActive = Boolean(pathname?.startsWith(logsHref));

  return (
    <div
      style={{
        display: 'flex',
        width: '100%',
        height: '100%',
        background: T.bg,
        fontFamily: T.fontSans,
      }}
    >
      <aside
        style={{
          width: SIDEBAR_WIDTH,
          borderRight: `1px solid ${T.border}`,
          display: 'flex',
          flexDirection: 'column',
          background: T.rail,
          boxSizing: 'border-box',
          flexShrink: 0,
        }}
      >
        <div
          style={{
            height: 46,
            minHeight: 46,
            display: 'flex',
            alignItems: 'center',
            padding: '0 14px',
            borderBottom: `1px solid ${T.border}`,
            boxSizing: 'border-box',
          }}
        >
          <span
            style={{
              ...CHROME_LABEL_TYPOGRAPHY,
              color: T.text1,
            }}
          >
            Develop
          </span>
        </div>

        <nav
          style={{
            flex: 1,
            overflowY: 'auto',
            overflowX: 'hidden',
            padding: '8px 6px',
          }}
        >
          <DevelopNavItem
            href={logsHref}
            active={logsActive}
            label="Logs"
            icon={<LogsIcon />}
          />
        </nav>
      </aside>

      <section
        style={{
          flex: 1,
          minWidth: 0,
          height: '100%',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          background: T.bg,
        }}
      >
        {children}
      </section>
    </div>
  );
}

function DevelopNavItem({
  href,
  active,
  label,
  icon,
}: {
  href: string;
  active: boolean;
  label: string;
  icon: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      style={{
        height: 32,
        borderRadius: 6,
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '0 10px',
        color: active ? T.text1 : T.text2,
        background: active ? 'var(--po-active)' : 'transparent',
        textDecoration: 'none',
        fontSize: 13,
        fontWeight: active ? 600 : 500,
        letterSpacing: 0,
        boxSizing: 'border-box',
      }}
      onMouseEnter={event => {
        if (!active) event.currentTarget.style.background = 'var(--po-hover)';
      }}
      onMouseLeave={event => {
        if (!active) event.currentTarget.style.background = 'transparent';
      }}
    >
      <span
        style={{
          width: 16,
          height: 16,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: active ? T.text1 : T.text3,
          flexShrink: 0,
        }}
      >
        {icon}
      </span>
      <span
        style={{
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {label}
      </span>
    </Link>
  );
}

function LogsIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <line x1="7" y1="9" x2="7.01" y2="9" />
      <line x1="10" y1="9" x2="17" y2="9" />
      <line x1="7" y1="13" x2="7.01" y2="13" />
      <line x1="10" y1="13" x2="17" y2="13" />
      <line x1="7" y1="17" x2="7.01" y2="17" />
      <line x1="10" y1="17" x2="14" y2="17" />
    </svg>
  );
}
