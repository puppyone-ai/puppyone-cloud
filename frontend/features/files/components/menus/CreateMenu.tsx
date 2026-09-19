'use client';

import { FileGlyphIcon } from '@/lib/fileIcons';
import { APP_Z_INDEX } from '@/lib/zIndex';
import type { MouseEvent as ReactMouseEvent, ReactNode } from 'react';
import { useLayoutEffect, useRef, useState } from 'react';

export type CreateType =
  | 'folder'
  | 'blank-json'
  | 'blank-markdown'
  | 'import-files'
  | 'import-url'
  | 'import-saas';

export interface CreateMenuProps {
  x: number;
  y: number;
  anchorLeft?: number;
  onClose: () => void;
  // When `accessOnly` is true, the menu skips the Create Blank /
  // Upload sections entirely and renders the access-provider list
  // (Notion, Gmail, Calendar, GitHub, MCP, Sandbox, etc.)
  // as the top-level menu — flat, no `New Access >` submenu trigger.
  // This is what the sidebar row `Expose as...` command uses: the
  // user already expressed the intent "create access for this folder",
  // so the picker shouldn't make them traverse a nested
  // submenu to *get* to the access list.
  accessOnly?: boolean;
  onCreateFolder: () => void;
  onCreateBlankJson: () => void;
  onCreateBlankMarkdown: () => void;
  onImportFromFiles: () => void;
  onImportFromUrl: () => void;
  onImportFromSaas: () => void;
  onImportNotion?: () => void;
  onConnectGitHub?: () => void;
  onImportGmail?: () => void;
  onImportDocs?: () => void;
  onImportCalendar?: () => void;
  onImportSheets?: () => void;
  onConnectSupabase?: () => void;
  onImportSearchConsole?: () => void;
  onCreateAgent?: () => void;
  onCreateMcp?: () => void;
  onCreateSandbox?: () => void;
  onCreateSshTerminal?: () => void;
}

interface MenuItemProps {
  icon?: ReactNode;
  label: string;
  sublabel?: string;
  onClick?: (e: ReactMouseEvent) => void;
  onMouseEnter?: (e: ReactMouseEvent<HTMLButtonElement>) => void;
  isActive?: boolean;
  hasSubmenu?: boolean;
  disabled?: boolean;
}

function MenuItem({ icon, label, sublabel, onClick, onMouseEnter, isActive, hasSubmenu, disabled }: MenuItemProps) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onPointerDown={(e) => {
        e.preventDefault();
        e.stopPropagation();
      }}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!disabled) onClick?.(e);
      }}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        width: 'calc(100% - 8px)',
        height: 32,
        border: 'none',
        appearance: 'none',
        fontFamily: 'inherit',
        padding: '0 12px',
        cursor: disabled ? 'default' : 'pointer',
        color: disabled ? 'var(--po-text-disabled)' : 'var(--po-text)',
        // Was 14px — bumped down to 13px to align with the explorer
        // row context menu and the surrounding tree text. 14px made
        // popup items read as visually heavier than the rows that
        // launched them, which felt off because the popups are a
        // sibling control (not a primary surface).
        fontSize: 13,
        transition: 'background 0.1s',
        background: isActive ? 'var(--po-hover)' : 'transparent',
        borderRadius: 6,
        margin: '0 4px',
        position: 'relative',
        opacity: disabled ? 0.55 : 1,
        textAlign: 'left',
      }}
      onMouseEnter={(e) => {
        if (!disabled) e.currentTarget.style.background = 'var(--po-hover)';
        onMouseEnter?.(e);
      }}
      onMouseLeave={(e) => {
        if (!isActive) e.currentTarget.style.background = 'transparent';
      }}
    >
      {icon && (
        <span style={{
          display: 'flex', width: 14, height: 14, alignItems: 'center', justifyContent: 'center',
          opacity: disabled ? 0.4 : 0.7,
          filter: disabled ? 'grayscale(1)' : undefined,
        }}>
          {icon}
        </span>
      )}
      <span style={{ flex: 1, whiteSpace: 'nowrap' }}>{label}</span>
      {sublabel && <span style={{ fontSize: 11, color: disabled ? 'var(--po-text-disabled)' : 'var(--po-text-subtle)', marginLeft: 8 }}>{sublabel}</span>}
      {hasSubmenu && (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.5 }}>
          <polyline points="9 18 15 12 9 6" />
        </svg>
      )}
    </button>
  );
}

function Divider() {
  return <div style={{ height: 1, background: 'var(--po-border)', margin: '4px 8px' }} />;
}

const MENU_EDGE_PADDING = 12;

const iconColor = 'var(--po-text-muted)';

const FolderIcon = () => (
  <FileGlyphIcon name="folder" type="folder" size={16} />
);

const JsonIcon = () => (
  <FileGlyphIcon name="Untitled.json" type="json" size={16} />
);

const MarkdownIcon = () => (
  <FileGlyphIcon name="Untitled.md" type="markdown" size={16} />
);

const UploadIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={iconColor} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="17 8 12 3 7 8" />
    <line x1="12" y1="3" x2="12" y2="15" />
  </svg>
);

// Plug-in icon kept around even though the "New Integration" parent
// row that referenced it has been retired (see comment in the
// connect submenu wrapper below). Treating it as a deletable
// orphan would lose a designed asset that pairs naturally with the
// integrations submenu the moment that entry is reinstated.
const ConnectIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={iconColor} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22v-5" />
    <path d="M9 8V2" />
    <path d="M15 8V2" />
    <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" />
  </svg>
);

const NotionIcon = () => (
  <svg width="14" height="14" viewBox="0 0 100 100" fill="none">
    <path d="M6.017 4.313l55.333 -4.087c6.797 -0.583 8.543 -0.19 12.817 2.917l17.663 12.443c2.913 2.14 3.883 2.723 3.883 5.053v68.243c0 4.277 -1.553 6.807 -6.99 7.193L24.467 99.967c-4.08 0.193 -6.023 -0.39 -8.16 -3.113L3.3 79.94c-2.333 -3.113 -3.3 -5.443 -3.3 -8.167V11.113c0 -3.497 1.553 -6.413 6.017 -6.8z" fill="var(--po-panel-raised)" />
    <path fillRule="evenodd" clipRule="evenodd" d="M61.35 0.227l-55.333 4.087C1.553 4.7 0 7.617 0 11.113v60.66c0 2.723 0.967 5.053 3.3 8.167l13.007 16.913c2.137 2.723 4.08 3.307 8.16 3.113l64.257 -3.89c5.433 -0.387 6.99 -2.917 6.99 -7.193V20.64c0 -2.21 -0.873 -2.847 -3.443 -4.733L74.167 3.143c-4.273 -3.107 -6.02 -3.5 -12.817 -2.917zM25.92 19.523c-5.247 0.353 -6.437 0.433 -9.417 -1.99L8.927 11.507c-0.77 -0.78 -0.383 -1.753 1.557 -1.947l53.193 -3.887c4.467 -0.39 6.793 1.167 8.54 2.527l9.123 6.61c0.39 0.197 1.36 1.36 0.193 1.36l-54.933 3.307 -0.68 0.047zM19.803 88.3V30.367c0 -2.53 0.777 -3.697 3.103 -3.893L86 22.78c2.14 -0.193 3.107 1.167 3.107 3.693v57.547c0 2.53 -0.39 4.67 -3.883 4.863l-60.377 3.5c-3.493 0.193 -5.043 -0.97 -5.043 -4.083zm59.6 -54.827c0.387 1.75 0 3.5 -1.75 3.7l-2.91 0.577v42.773c-2.527 1.36 -4.853 2.137 -6.797 2.137 -3.107 0 -3.883 -0.973 -6.21 -3.887l-19.03 -29.94v28.967l6.02 1.363s0 3.5 -4.857 3.5l-13.39 0.777c-0.39 -0.78 0 -2.723 1.357 -3.11l3.497 -0.97v-38.3L30.48 40.667c-0.39 -1.75 0.58 -4.277 3.3 -4.473l14.367 -0.967 19.8 30.327v-26.83l-5.047 -0.58c-0.39 -2.143 1.163 -3.7 3.103 -3.89l13.4 -0.78z" fill="var(--po-text)" />
  </svg>
);

const GitHubIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill={iconColor}>
    <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
  </svg>
);

const GmailIcon = () => <img src="/icons/gmail.svg" alt="Gmail" width={14} height={14} style={{ display: 'block' }} />;
const DocsIcon = () => <img src="/icons/google_doc.svg" alt="Google Docs" width={14} height={14} style={{ display: 'block' }} />;
const CalendarIcon = () => <img src="/icons/google_calendar.svg" alt="Google Calendar" width={14} height={14} style={{ display: 'block' }} />;
const SheetsIcon = () => <img src="/icons/google_sheet.svg" alt="Google Sheets" width={14} height={14} style={{ display: 'block' }} />;

const SupabaseIcon = () => (
  <svg width="14" height="14" viewBox="0 0 109 113" fill="none">
    <path d="M63.7076 110.284C60.8481 113.885 55.0502 111.912 54.9813 107.314L53.9738 40.0627L99.1935 40.0627C107.384 40.0627 111.952 49.5228 106.859 55.9374L63.7076 110.284Z" fill="url(#paint0_linear)" />
    <path d="M63.7076 110.284C60.8481 113.885 55.0502 111.912 54.9813 107.314L53.9738 40.0627L99.1935 40.0627C107.384 40.0627 111.952 49.5228 106.859 55.9374L63.7076 110.284Z" fill="url(#paint1_linear)" fillOpacity="0.2" />
    <path d="M45.317 2.07103C48.1765 -1.53037 53.9745 0.442937 54.0434 5.041L54.4849 72.2922H9.83113C1.64038 72.2922 -2.92775 62.8321 2.1655 56.4175L45.317 2.07103Z" fill="#3ECF8E" />
    <defs>
      <linearGradient id="paint0_linear" x1="53.9738" y1="54.974" x2="94.1635" y2="71.8295" gradientUnits="userSpaceOnUse">
        <stop stopColor="#249361" />
        <stop offset="1" stopColor="#3ECF8E" />
      </linearGradient>
      <linearGradient id="paint1_linear" x1="36.1558" y1="30.578" x2="54.4844" y2="65.0806" gradientUnits="userSpaceOnUse">
        <stop />
        <stop offset="1" stopOpacity="0" />
      </linearGradient>
    </defs>
  </svg>
);

const SearchConsoleIcon = () => <span style={{ fontSize: 14 }}>📊</span>;

// Local folder sync and Chat Agent menu rows were removed in the
// 2026-05-08 cleanup; Chat Agent is now a per-scope built-in.

const McpIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--po-accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="3" width="20" height="14" rx="2" />
    <line x1="8" y1="21" x2="16" y2="21" />
    <line x1="12" y1="17" x2="12" y2="21" />
  </svg>
);

const SandboxIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--po-warning)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="4 17 10 11 4 5" />
    <line x1="12" y1="19" x2="20" y2="19" />
  </svg>
);

export function CreateMenu({
  x,
  y,
  anchorLeft,
  onClose,
  accessOnly,
  onCreateFolder,
  onCreateBlankJson,
  onCreateBlankMarkdown,
  onImportFromFiles,
  onImportFromUrl,
  onImportFromSaas,
  onImportNotion,
  onConnectGitHub,
  onImportGmail,
  onImportDocs,
  onImportCalendar,
  onImportSheets,
  onConnectSupabase,
  onImportSearchConsole,
  onCreateAgent,
  onCreateMcp,
  onCreateSandbox,
  onCreateSshTerminal,
}: CreateMenuProps) {
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const element = menuRef.current;
    if (!element) return;

    const menuRect = element.getBoundingClientRect();
    const menuWidth = Math.max(menuRect.width, element.offsetWidth);
    const menuHeight = Math.max(menuRect.height, element.offsetHeight);
    const viewportHeight = window.innerHeight;
    const viewportWidth = window.innerWidth;
    const pad = 12;
    let left = anchorLeft ?? x;
    if (left + menuWidth > viewportWidth - pad) {
      left = Math.max(pad, viewportWidth - menuWidth - pad);
    }

    let top = y;
    if (top + menuHeight > viewportHeight - pad) {
      top = Math.max(pad, viewportHeight - menuHeight - pad);
    }

    setPosition({ top, left });
  }, [x, y, anchorLeft]);

  return (
    <div
      ref={menuRef}
      role="menu"
      onPointerDown={(e) => {
        e.stopPropagation();
      }}
      onClick={(e) => {
        e.stopPropagation();
      }}
      style={{
        position: 'fixed',
        top: position?.top ?? y,
        left: position?.left ?? x,
        zIndex: APP_Z_INDEX.popover,
        background: 'var(--po-overlay)',
        backdropFilter: 'blur(20px)',
        border: '1px solid var(--po-border)',
        borderRadius: 8,
        padding: '4px 0',
        minWidth: accessOnly ? 240 : 176,
        maxHeight: 'calc(100vh - 24px)',
        overflowY: 'auto',
        overflowX: 'hidden',
        boxShadow: '0 8px 32px var(--po-shadow)',
        visibility: position ? 'visible' : 'hidden',
      }}
    >
      {!accessOnly && (
        <>
          <div style={{ padding: '6px 12px 2px', fontSize: 10.5, fontWeight: 500, color: 'var(--po-text-subtle)', letterSpacing: 0 }}>
            Create blank
          </div>

          <MenuItem icon={<FolderIcon />} label="Folder" onClick={() => { onCreateFolder(); onClose(); }} />
          <MenuItem icon={<MarkdownIcon />} label="Markdown" onClick={() => { onCreateBlankMarkdown(); onClose(); }} />
          <MenuItem icon={<JsonIcon />} label="JSON" onClick={() => { onCreateBlankJson(); onClose(); }} />

          <Divider />

          {/* Add content splits into Upload (local files) and Import (a one-shot
              snapshot from an external source). Both are first-class entries —
              the Import callbacks already exist (used by the accessOnly picker);
              this surfaces them in the default + menu as peers to Upload. */}
          <MenuItem
            icon={<UploadIcon />}
            label="Upload files"
            sublabel="From this device"
            onClick={() => { onImportFromFiles(); onClose(); }}
          />
          <MenuItem
            icon={
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="10" stroke={iconColor} strokeWidth="1.5" />
                <path d="M2 12h20" stroke={iconColor} strokeWidth="1.5" />
                <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" stroke={iconColor} strokeWidth="1.5" />
              </svg>
            }
            label="Import from URL"
            sublabel="Web page"
            onClick={() => { onImportFromUrl(); onClose(); }}
          />
          <MenuItem
            icon={
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={iconColor} strokeWidth="1.5">
                <rect x="3" y="3" width="7" height="7" rx="1.5" />
                <rect x="14" y="3" width="7" height="7" rx="1.5" />
                <rect x="3" y="14" width="7" height="7" rx="1.5" />
                <rect x="14" y="14" width="7" height="7" rx="1.5" />
              </svg>
            }
            label="Import from a source…"
            sublabel="Notion, Google, GitHub…"
            onClick={() => { onImportFromSaas(); onClose(); }}
          />
        </>
      )}

      <div style={{ position: 'relative' }}>
        {/*
          The "New Integration" parent row used to live here as a
          hover-triggered MenuItem that opened the integrations
          submenu. We removed it from the visible menu because:
            1. Every node in the explorer already exposes a
               scoped `Expose as...` command that opens the
               same integrations panel scoped to that node, so
               this entry was a duplicate path.
            2. The visual treatment (chevron + nested floating
               card hovering off the parent row) was inconsistent
               with the rest of the create menu, which is flat.

          We deliberately keep the submenu *content* below mounted —
          it's the canonical "all integrations" picker we still
          render in `accessOnly` mode (the sidebar row `Expose as...`
          uses CreateMenu with accessOnly=true and renders this
          block flat at the top level). Removing the content here
          would also delete that picker, which is a real product
          asset.

          The integrations picker is intentionally hidden from the
          regular Create menu. It renders only in `accessOnly` mode,
          which is opened by the sidebar row `Expose as...` command after the user
          has already expressed "create access here".
        */}
        {accessOnly && (
          <div
            style={{
              padding: '4px 0',
              minWidth: 240,
            }}
          >
            <div style={{ padding: '6px 16px 2px', fontSize: 10, fontWeight: 600, color: 'var(--po-text-subtle)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Connect a source
            </div>
            {onImportNotion && <MenuItem icon={<NotionIcon />} label="Notion" sublabel="Pages" onClick={() => { onImportNotion(); onClose(); }} />}
            {onImportGmail && <MenuItem icon={<GmailIcon />} label="Gmail" sublabel="Emails" onClick={() => { onImportGmail(); onClose(); }} />}
            {onImportCalendar && <MenuItem icon={<CalendarIcon />} label="Google Calendar" sublabel="Events" onClick={() => { onImportCalendar(); onClose(); }} />}
            {onImportDocs && <MenuItem icon={<DocsIcon />} label="Google Docs" sublabel="Document" onClick={() => { onImportDocs(); onClose(); }} />}
            {onImportSheets && <MenuItem icon={<SheetsIcon />} label="Google Sheets" sublabel="Spreadsheet" onClick={() => { onImportSheets(); onClose(); }} />}
            {onConnectSupabase && <MenuItem icon={<SupabaseIcon />} label="Supabase" sublabel="Database" onClick={() => { onConnectSupabase(); onClose(); }} />}
            <MenuItem
              icon={
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <circle cx="12" cy="12" r="10" stroke={iconColor} strokeWidth="1.5" />
                  <path d="M2 12h20" stroke={iconColor} strokeWidth="1.5" />
                  <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" stroke={iconColor} strokeWidth="1.5" />
                </svg>
              }
              label="Web Page"
              sublabel="URL"
              onClick={() => { onImportFromUrl(); onClose(); }}
            />
            {onConnectGitHub && (
              <MenuItem
                icon={<GitHubIcon />}
                label="Connect GitHub"
                sublabel="Branch sync"
                onClick={() => { onConnectGitHub(); onClose(); }}
              />
            )}
            <MenuItem icon={<SearchConsoleIcon />} label="Google Search Console" sublabel="Coming soon" disabled />

            <Divider />

            <div style={{ padding: '6px 16px 2px', fontSize: 10, fontWeight: 600, color: 'var(--po-text-subtle)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Connect via terminal
            </div>
            <MenuItem
              icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--po-text-subtle)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" /></svg>}
              label="SSH Terminal"
              sublabel="Remote dev over SSH"
              onClick={() => {
                onCreateSshTerminal?.();
                onClose();
              }}
            />

            <Divider />

            <div style={{ padding: '6px 16px 2px', fontSize: 10, fontWeight: 600, color: 'var(--po-text-subtle)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Expose data
            </div>
            <MenuItem
              icon={<McpIcon />}
              label="MCP Server"
              sublabel="Expose this folder to MCP clients"
              onClick={() => {
                onCreateMcp?.();
                onClose();
              }}
            />
            <MenuItem
              icon={<SandboxIcon />}
              label="Sandbox"
              sublabel="Run tools with this folder mounted"
              onClick={() => {
                // Sandbox is a scope-level Remote Dev (SSH) card on the Access
                // page, not a connector — route to it like the SSH Terminal
                // entry (onCreateSandbox would open a sync-config panel, which
                // does not fit a sandbox).
                onCreateSshTerminal?.();
                onClose();
              }}
            />

            {/* "More Sources…" is the open-the-empty-picker fallback
                — useful from `+ → New Access` when the user is
                exploring, but pointless from a scoped `Expose as...`
                command (the user has already declared intent against
                a specific folder, so an empty picker is a step
                backwards).  Hide it in accessOnly mode. */}
            {!accessOnly && (
              <>
                <Divider />

                <MenuItem
                  icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={iconColor} strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>}
                  label="More Sources..."
                  onClick={() => { onImportFromSaas(); onClose(); }}
                />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
