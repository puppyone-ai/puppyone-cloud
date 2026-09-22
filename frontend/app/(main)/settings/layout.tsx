'use client';

/**
 * Settings layout — chrome for the global `/settings/*` routes
 * (currently just `/settings/connect`).
 *
 * Visual grammar matches the rest of `/(main)`:
 *
 *   - Page-level header is a single 46px row with a hairline border,
 *     13px / 500 / `var(--po-text)` title in Geist Sans. Same as
 *     `AccessHeader` and the project Settings page.
 *   - Secondary sidebar uses the same chrome sizing as AppSidebar.
 *     Before this, navigating Settings → anywhere else visibly
 *     snapped the page to the screen edge. Pages render flush against
 *     the AppSidebar now, like every other surface.
 *   - Collapsed nav active state is the same neutral lift as the
 *     AppSidebar (white/[0.06]), not the blue tint the old version
 *     used. Blue is reserved for the sidebar's `active accent bar`
 *     so it stays a single distinctive signal.
 */

import React, { useState, useRef, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

const MIN_WIDTH = 200;
const MAX_WIDTH = 320;
const DEFAULT_WIDTH = MIN_WIDTH;
const COLLAPSED_WIDTH = 47;

// Local design tokens. Same family as the project Access page +
// the project Settings page so all three surfaces share a single
// font stack / border alpha / text scale.
const T = {
  bg: 'var(--po-canvas)',
  rail: 'var(--po-sidebar)',
  border: 'var(--po-border)',
  text1: 'var(--po-text)',
  text2: 'var(--po-text-muted)',
  text3: 'var(--po-text-disabled)',
  fontSans:
    'var(--po-font-sans)',
} as const;

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_WIDTH);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const sidebarRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (isCollapsed) return;
      e.preventDefault();
      setIsResizing(true);
    },
    [isCollapsed]
  );

  useEffect(() => {
    if (!isResizing) return;

    const handleMouseMove = (e: MouseEvent) => {
      if (!sidebarRef.current) return;
      const rect = sidebarRef.current.getBoundingClientRect();
      const newWidth = e.clientX - rect.left;
      const clampedWidth = Math.min(Math.max(newWidth, MIN_WIDTH), MAX_WIDTH);
      setSidebarWidth(clampedWidth);
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isResizing]);

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
      {/* Secondary sidebar — flush against the AppSidebar to its
          left. No outer card / margin / radius; this is just the
          page splitting into a rail + a content pane, same as
          /access and /history. */}
      <aside
        ref={sidebarRef}
        style={{
          width: isCollapsed ? COLLAPSED_WIDTH : sidebarWidth,
          borderRight: `1px solid ${T.border}`,
          display: 'flex',
          flexDirection: 'column',
          background: T.rail,
          boxSizing: 'border-box',
          position: 'relative',
          flexShrink: 0,
          transition: isResizing ? 'none' : 'width 0.2s ease',
        }}
      >
        {/* Header — 46px row, single border-bottom, matches every
            other page header in /(main). 13px / 500 / var(--po-text) in
            Geist Sans. The collapse / expand button hover-fades in,
            same pattern the AppSidebar uses for its own collapse
            toggle. */}
        <div
          style={{
            height: 46,
            minHeight: 46,
            maxHeight: 46,
            display: 'flex',
            alignItems: 'center',
            justifyContent: isCollapsed ? 'center' : 'space-between',
            padding: isCollapsed ? '0' : '0 8px 0 14px',
            borderBottom: `1px solid ${T.border}`,
            boxSizing: 'border-box',
          }}
          className='group/settings-header'
        >
          {isCollapsed ? (
            <button
              type='button'
              onClick={() => setIsCollapsed(false)}
              title='Expand sidebar'
              aria-label='Expand sidebar'
              style={collapseToggleStyle}
              onMouseEnter={onCollapseEnter}
              onMouseLeave={onCollapseLeave}
            >
              <PanelIcon />
            </button>
          ) : (
            <>
              <span
                style={{
                  fontSize: 13,
                  fontWeight: 500,
                  color: 'var(--po-text)',
                  letterSpacing: 0,
                }}
              >
                Settings
              </span>
              <button
                type='button'
                onClick={() => setIsCollapsed(true)}
                title='Collapse sidebar'
                aria-label='Collapse sidebar'
                style={collapseToggleStyle}
                onMouseEnter={onCollapseEnter}
                onMouseLeave={onCollapseLeave}
              >
                <PanelIcon />
              </button>
            </>
          )}
        </div>

        {/* Expanded nav — single column of 32px rows. Same row spec
            as the AppSidebar's nav. */}
        {!isCollapsed && (
          <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', paddingTop: 8 }}>
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 1,
                padding: '0 6px',
              }}
            >
              <NavItem
                href='/settings/appearance'
                active={Boolean(pathname?.startsWith('/settings/appearance'))}
                label='Appearance'
                icon={<AppearanceIcon />}
              />
              <NavItem
                href='/settings/connect'
                active={Boolean(pathname?.startsWith('/settings/connect'))}
                label='Integrations'
                icon={<IntegrationsIcon />}
              />
              <NavItem
                href='/settings/about'
                active={Boolean(pathname?.startsWith('/settings/about'))}
                label='About'
                icon={<AboutIcon />}
              />
            </div>
          </div>
        )}

        {/* Collapsed nav — 32×32 icon buttons, symmetric padding all
            around (matches the AppSidebar's collapsed nav). Active
            state is the same neutral lift, not blue. */}
        {isCollapsed && (
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              padding: '8px 0',
              gap: 6,
            }}
          >
            <CollapsedNavItem
              href='/settings/appearance'
              active={Boolean(pathname?.startsWith('/settings/appearance'))}
              title='Appearance'
              icon={<AppearanceIcon />}
            />
            <CollapsedNavItem
              href='/settings/connect'
              active={Boolean(pathname?.startsWith('/settings/connect'))}
              title='Integrations'
              icon={<IntegrationsIcon />}
            />
            <CollapsedNavItem
              href='/settings/about'
              active={Boolean(pathname?.startsWith('/settings/about'))}
              title='About'
              icon={<AboutIcon />}
            />
          </div>
        )}

        {/* Resize handle — same hover-revealed bar the AppSidebar
            uses on its right edge. */}
        {!isCollapsed && (
          <div
            onMouseDown={handleMouseDown}
            style={{
              position: 'absolute',
              top: 0,
              right: -2,
              width: 4,
              height: '100%',
              cursor: 'col-resize',
              zIndex: 10,
              background: isResizing ? 'var(--po-active)' : 'transparent',
              transition: 'background 0.15s',
            }}
            onMouseEnter={e => {
              if (!isResizing) e.currentTarget.style.background = 'var(--po-active)';
            }}
            onMouseLeave={e => {
              if (!isResizing) e.currentTarget.style.background = 'transparent';
            }}
            role='separator'
            aria-orientation='vertical'
          />
        )}
      </aside>

      {/* Main content pane — flush, no margins, no radius. */}
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

// ─── Sub-components ──────────────────────────────────────────────────

const collapseToggleStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 30,
  height: 30,
  background: 'transparent',
  border: 'none',
  borderRadius: 5,
  cursor: 'pointer',
  color: 'var(--po-text-subtle)',
  transition: 'background 0.15s, color 0.15s',
};

function onCollapseEnter(e: React.MouseEvent<HTMLButtonElement>) {
  e.currentTarget.style.background = 'var(--po-hover)';
  e.currentTarget.style.color = 'var(--po-text)';
}
function onCollapseLeave(e: React.MouseEvent<HTMLButtonElement>) {
  e.currentTarget.style.background = 'transparent';
  e.currentTarget.style.color = 'var(--po-text-subtle)';
}

function PanelIcon() {
  return (
    <svg
      width='14'
      height='14'
      viewBox='0 0 24 24'
      fill='none'
      stroke='currentColor'
      strokeWidth='1.5'
      strokeLinecap='round'
      strokeLinejoin='round'
    >
      <rect x='3' y='3' width='18' height='18' rx='2' />
      <line x1='9' y1='3' x2='9' y2='21' />
    </svg>
  );
}

function AppearanceIcon() {
  return (
    <svg
      width='15'
      height='15'
      viewBox='0 0 24 24'
      fill='none'
      stroke='currentColor'
      strokeWidth='2'
      strokeLinecap='round'
      strokeLinejoin='round'
    >
      <circle cx='12' cy='12' r='4' />
      <path d='M12 2v2' />
      <path d='M12 20v2' />
      <path d='m4.93 4.93 1.41 1.41' />
      <path d='m17.66 17.66 1.41 1.41' />
      <path d='M2 12h2' />
      <path d='M20 12h2' />
      <path d='m6.34 17.66-1.41 1.41' />
      <path d='m19.07 4.93-1.41 1.41' />
    </svg>
  );
}

// Integrations / connectors glyph. Matches the chain-link family used
// by the project Access icon — consistent semantic ("things connected
// to your workspace") across every surface that talks about
// integrations.
function IntegrationsIcon() {
  return (
    <svg
      width='15'
      height='15'
      viewBox='0 0 24 24'
      fill='none'
      stroke='currentColor'
      strokeWidth='2'
      strokeLinecap='round'
      strokeLinejoin='round'
    >
      <path d='M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71' />
      <path d='M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71' />
    </svg>
  );
}

function AboutIcon() {
  return (
    <svg
      width='15'
      height='15'
      viewBox='0 0 24 24'
      fill='none'
      stroke='currentColor'
      strokeWidth='2'
      strokeLinecap='round'
      strokeLinejoin='round'
    >
      <circle cx='12' cy='12' r='9' />
      <path d='M12 11v5' />
      <path d='M12 8h.01' />
    </svg>
  );
}

// Expanded nav row. 32px tall, 13px label, 10px x-padding, 10px gap.
// Lifted directly from the AppSidebar's nav row spec so the two
// rails read as one font / one row height when they're side-by-side.
function NavItem({
  active,
  href,
  label,
  icon,
}: {
  active?: boolean;
  href: string;
  label: string;
  icon: React.ReactNode;
}) {
  const [hovered, setHovered] = useState(false);

  return (
    <Link
      href={href}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        position: 'relative',
        height: 32,
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '0 10px',
        borderRadius: 6,
        cursor: 'pointer',
        background: active
          ? 'var(--po-selected)'
          : hovered
          ? 'var(--po-hover)'
          : 'transparent',
        border: 'none',
        width: '100%',
        textDecoration: 'none',
        transition: 'background 0.15s, color 0.15s',
        boxSizing: 'border-box',
        color: active ? T.text1 : hovered ? T.text1 : T.text2,
        fontFamily: T.fontSans,
      }}
    >
      {/* Active accent bar — same cyan #22d3ee mark the AppSidebar
          uses on its active row. Mirrors the visual anchor the user
          already learned in the workspace rail. */}
      {active && (
        <span
          aria-hidden
          style={{
            position: 'absolute',
            left: -6,
            top: 5,
            bottom: 5,
            width: 2,
            borderRadius: 1,
            background: 'var(--po-accent)',
            boxShadow: '0 0 6px color-mix(in srgb, var(--po-accent) 40%, transparent)',
            pointerEvents: 'none',
          }}
        />
      )}

      <span
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 15,
          height: 15,
          flexShrink: 0,
          color: active ? T.text1 : T.text2,
        }}
      >
        {icon}
      </span>

      <span
        style={{
          flex: 1,
          fontSize: 13,
          fontWeight: active ? 500 : 400,
          color: 'inherit',
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

// Collapsed-nav row. 32×32 button, neutral hover/active. Matches the
// AppSidebar's `collapsedBtnClass` exactly so the two rails feel like
// the same widget collapsed.
function CollapsedNavItem({
  active,
  href,
  title,
  icon,
}: {
  active?: boolean;
  href: string;
  title: string;
  icon: React.ReactNode;
}) {
  const [hovered, setHovered] = useState(false);

  return (
    <Link
      href={href}
      title={title}
      aria-label={title}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        width: 32,
        height: 32,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: active
          ? 'var(--po-selected)'
          : hovered
          ? 'var(--po-hover)'
          : 'transparent',
        borderRadius: 6,
        cursor: 'pointer',
        color: active || hovered ? T.text1 : T.text2,
        transition: 'background 0.15s, color 0.15s',
        textDecoration: 'none',
      }}
    >
      <span style={{ width: 18, height: 18, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {React.isValidElement(icon)
          ? React.cloneElement(icon as React.ReactElement, { width: 18, height: 18 } as any)
          : icon}
      </span>
    </Link>
  );
}
