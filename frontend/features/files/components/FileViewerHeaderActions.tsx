'use client';

import { EditorSaveButton } from '@/components/editors/EditorSaveButton';
import type { HtmlArtifactMode } from '@/components/editors/html/HtmlArtifactPreview';
import type { MarkdownViewMode } from '@/components/editors/markdown';
import type { CsvViewMode } from '@/components/editors/spreadsheet/CsvTableViewer';
import type { EditorType } from '@/components/ProjectsHeader';
import styles from '@/features/files/components/FileViewerHeaderActions.module.css';
import { downloadNode } from '@/lib/contentTreeApi';
import type { GenericViewerId, SpecialViewerId } from '@/lib/fileFormats/types';
import type { SaveStatus } from '@/lib/hooks/useManualSave';
import { APP_Z_INDEX } from '@/lib/zIndex';
import type { FC, MouseEvent as ReactMouseEvent, ReactNode } from 'react';
import { Children, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

type HeaderViewerId = GenericViewerId | SpecialViewerId;

interface FileViewerHeaderActionsProps {
  projectId: string;
  filePath: string;
  viewerId: HeaderViewerId | null;
  editable: boolean;
  markdownViewMode: MarkdownViewMode;
  onMarkdownViewModeChange: (mode: MarkdownViewMode) => void;
  saveStatus: SaveStatus;
  onSave: () => void;
  editorType: EditorType;
  onEditorTypeChange: (mode: EditorType) => void;
  htmlMode: HtmlArtifactMode;
  onHtmlModeChange: (mode: HtmlArtifactMode) => void;
  csvViewMode: CsvViewMode;
  onCsvViewModeChange: (mode: CsvViewMode) => void;
  actionsSlot?: ReactNode;
}

interface ModeOption<TMode extends string> {
  value: TMode;
  label: string;
  Icon: FC;
}

const MARKDOWN_OPTIONS: ModeOption<MarkdownViewMode>[] = [
  { value: 'wysiwyg', label: 'Live view', Icon: PencilIcon },
  { value: 'source', label: 'Source code', Icon: CodeIcon },
];

const JSON_OPTIONS: ModeOption<EditorType>[] = [
  { value: 'table', label: 'Table view', Icon: TableIcon },
  { value: 'monaco', label: 'Source', Icon: BracesIcon },
];

const HTML_OPTIONS: ModeOption<HtmlArtifactMode>[] = [
  { value: 'preview', label: 'Preview', Icon: EyeIcon },
  { value: 'source', label: 'Source', Icon: CodeIcon },
];

const CSV_OPTIONS: ModeOption<CsvViewMode>[] = [
  { value: 'edit', label: 'Edit table', Icon: PencilIcon },
  { value: 'preview', label: 'Preview table', Icon: TableIcon },
  { value: 'source', label: 'Source', Icon: CodeIcon },
];

export function FileViewerHeaderActions({
  projectId,
  filePath,
  viewerId,
  editable,
  markdownViewMode,
  onMarkdownViewModeChange,
  saveStatus,
  onSave,
  editorType,
  onEditorTypeChange,
  htmlMode,
  onHtmlModeChange,
  csvViewMode,
  onCsvViewModeChange,
  actionsSlot,
}: FileViewerHeaderActionsProps) {
  if (!viewerId) return null;

  const actions =
    actionsSlot !== undefined ? actionsSlot : <FileActionsMenu projectId={projectId} filePath={filePath} />;
  const resolvedMarkdownViewMode: MarkdownViewMode =
    markdownViewMode === 'source' ? 'source' : 'wysiwyg';
  const saveButton = editable ? <EditorSaveButton status={saveStatus} onSave={onSave} /> : null;

  if (viewerId === 'markdown-editor') {
    return (
      <HeaderActionGroup>
        {saveButton}
        <ModePicker mode={resolvedMarkdownViewMode} onChange={onMarkdownViewModeChange} options={MARKDOWN_OPTIONS} />
        {actions}
      </HeaderActionGroup>
    );
  }

  if (viewerId === 'plain-text') {
    return (
      <HeaderActionGroup>
        {saveButton}
        {actions}
      </HeaderActionGroup>
    );
  }

  if (viewerId === 'csv-table') {
    return (
      <HeaderActionGroup>
        {saveButton}
        <ModePicker mode={csvViewMode} onChange={onCsvViewModeChange} options={CSV_OPTIONS} />
        {actions}
      </HeaderActionGroup>
    );
  }

  if (viewerId === 'json-table') {
    return (
      <HeaderActionGroup>
        <ModePicker mode={editorType} onChange={onEditorTypeChange} options={JSON_OPTIONS} />
        {actions}
      </HeaderActionGroup>
    );
  }

  if (viewerId === 'html-artifact') {
    return (
      <HeaderActionGroup>
        <span
          style={{
            color: 'var(--po-text-subtle)',
            fontSize: 12,
            whiteSpace: 'nowrap',
          }}
        >
          Scripts disabled
        </span>
        <ModePicker mode={htmlMode} onChange={onHtmlModeChange} options={HTML_OPTIONS} />
        {actions}
      </HeaderActionGroup>
    );
  }

  return (
    <HeaderActionGroup>
      {saveButton}
      {actions}
    </HeaderActionGroup>
  );
}

function HeaderActionGroup({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const groupRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const controlsId = useId();
  const compactMenu = Children.toArray(children).length > 1;

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!groupRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', dismiss);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  return (
    <div ref={groupRef} className={styles.group} data-compact-menu={compactMenu} data-open={open}>
      {compactMenu && (
        <button ref={triggerRef} type='button' className={styles.trigger} title='File controls'
          aria-label='File controls' aria-expanded={open} aria-controls={controlsId}
          onClick={() => setOpen(value => !value)}>
          <MoreIcon />
        </button>
      )}
      <div id={controlsId} className={styles.controls}>{children}</div>
    </div>
  );
}

function FileActionsMenu({
  projectId,
  filePath,
}: {
  projectId: string;
  filePath: string;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const [hovered, setHovered] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const updatePosition = () => {
    const button = buttonRef.current;
    if (!button) return;
    const rect = button.getBoundingClientRect();
    const menuWidth = 156;
    const left = Math.max(8, Math.min(window.innerWidth - menuWidth - 8, rect.right - menuWidth));
    setPos({ top: rect.bottom + 4, left });
  };

  useEffect(() => {
    if (!open) return;
    updatePosition();

    const onDocClick = (event: MouseEvent) => {
      const target = event.target as Node;
      if (buttonRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    const onReposition = () => updatePosition();

    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', onReposition);
    window.addEventListener('scroll', onReposition, true);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onReposition);
      window.removeEventListener('scroll', onReposition, true);
    };
  }, [open]);

  const handleDownload = async () => {
    setOpen(false);
    try {
      await downloadNode(projectId, filePath);
    } catch (error) {
      console.error('[FileActionsMenu] Failed to download file:', error);
    }
  };

  const handleCopyPath = async () => {
    setOpen(false);
    try {
      await navigator.clipboard.writeText(filePath);
    } catch (error) {
      console.error('[FileActionsMenu] Failed to copy path:', error);
    }
  };

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => {
          if (!open) updatePosition();
          setOpen((value) => !value);
        }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        title="File actions"
        aria-haspopup="menu"
        aria-expanded={open}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 30,
          height: 30,
          borderRadius: 5,
          background: open || hovered ? 'var(--po-hover)' : 'transparent',
          border: 'none',
          color: open || hovered ? 'var(--po-text-muted)' : 'var(--po-text-disabled)',
          cursor: 'pointer',
          transition: 'background 0.12s ease, color 0.12s ease',
        }}
      >
        <MoreIcon />
      </button>

      {open && pos && typeof document !== 'undefined' && createPortal(
        <div
          ref={menuRef}
          role="menu"
          style={{
            position: 'fixed',
            top: pos.top,
            left: pos.left,
            minWidth: 156,
            padding: 4,
            background: 'var(--po-panel-raised)',
            border: '1px solid var(--po-border)',
            borderRadius: 6,
            boxShadow: '0 14px 34px var(--po-shadow)',
            zIndex: APP_Z_INDEX.popover,
          }}
        >
          <ActionMenuItem
            label="Download"
            icon={<DownloadIcon />}
            onClick={handleDownload}
          />
          <ActionMenuItem
            label="Copy path"
            icon={<CopyIcon />}
            onClick={handleCopyPath}
          />
        </div>,
        document.body,
      )}
    </>
  );
}

function ActionMenuItem({
  icon,
  label,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  const [hovered, setHovered] = useState(false);

  return (
    <button
      type="button"
      role="menuitem"
      onClick={(event: ReactMouseEvent) => {
        event.stopPropagation();
        onClick();
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        width: '100%',
        height: 30,
        padding: '0 8px',
        background: hovered ? 'var(--po-hover)' : 'transparent',
        border: 'none',
        borderRadius: 4,
        color: 'var(--po-text-muted)',
        fontSize: 12.5,
        fontWeight: 400,
        textAlign: 'left',
        cursor: 'pointer',
        transition: 'background 0.1s ease',
      }}
    >
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 14,
          height: 14,
          color: 'var(--po-text-muted)',
          flexShrink: 0,
        }}
      >
        {icon}
      </span>
      <span style={{ flex: 1, whiteSpace: 'nowrap' }}>{label}</span>
    </button>
  );
}

function ModePicker<TMode extends string>({
  mode,
  onChange,
  options,
}: {
  mode: TMode;
  onChange: (mode: TMode) => void;
  options: readonly ModeOption<TMode>[];
}) {
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (event: MouseEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const current = options.find((option) => option.value === mode) ?? options[0];
  if (!current) return null;
  const TriggerIcon = current.Icon;

  return (
    <div ref={wrapperRef} style={{ position: 'relative', display: 'inline-flex' }}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        title={`View mode: ${current.label}`}
        aria-haspopup="menu"
        aria-expanded={open}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 30,
          height: 30,
          borderRadius: 5,
          background: open || hovered ? 'var(--po-hover)' : 'transparent',
          border: 'none',
          color: open || hovered ? 'var(--po-text-muted)' : 'var(--po-text-disabled)',
          cursor: 'pointer',
          transition: 'background 0.12s ease, color 0.12s ease',
        }}
      >
        <TriggerIcon />
      </button>

      {open && (
        <div
          role="menu"
          style={{
            position: 'absolute',
            top: 'calc(100% + 4px)',
            right: 0,
            minWidth: 156,
            padding: 4,
            background: 'var(--po-panel-raised)',
            border: '1px solid var(--po-border)',
            borderRadius: 6,
            boxShadow: '0 14px 34px var(--po-shadow)',
            zIndex: APP_Z_INDEX.popover,
          }}
        >
          {options.map((option) => (
            <ModeOptionRow
              key={option.value}
              option={option}
              active={option.value === mode}
              onSelect={() => {
                onChange(option.value);
                setOpen(false);
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ModeOptionRow<TMode extends string>({
  option,
  active,
  onSelect,
}: {
  option: ModeOption<TMode>;
  active: boolean;
  onSelect: () => void;
}) {
  const { Icon } = option;
  const [hovered, setHovered] = useState(false);

  return (
    <button
      type="button"
      role="menuitem"
      onClick={onSelect}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        width: '100%',
        padding: '5px 8px',
        background: hovered ? 'var(--po-hover)' : 'transparent',
        border: 'none',
        borderRadius: 4,
        color: 'var(--po-text-muted)',
        fontSize: 12.5,
        fontWeight: 400,
        textAlign: 'left',
        cursor: 'pointer',
        transition: 'background 0.1s ease',
      }}
    >
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 14,
          height: 14,
          color: active ? 'var(--po-text)' : 'var(--po-text-muted)',
          flexShrink: 0,
        }}
      >
        <Icon />
      </span>
      <span style={{ flex: 1 }}>{option.label}</span>
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 14,
          height: 14,
          color: 'var(--po-text-muted)',
          flexShrink: 0,
        }}
      >
        {active && <CheckMini />}
      </span>
    </button>
  );
}

function PencilIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
    </svg>
  );
}

function CodeIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
    </svg>
  );
}

function TableIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <line x1="3" y1="9" x2="21" y2="9" />
      <line x1="3" y1="15" x2="21" y2="15" />
      <line x1="9" y1="3" x2="9" y2="21" />
    </svg>
  );
}

function BracesIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M7 4C5.5 4 4 5 4 7s1.5 2.5 1.5 5S4 17 4 17c0 2 1.5 3 3 3" />
      <path d="M17 4c1.5 0 3 1 3 3s-1.5 2.5-1.5 5 1.5 5 1.5 5c0 2-1.5 3-3 3" />
    </svg>
  );
}

function EyeIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function CheckMini() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function MoreIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
      <circle cx="12" cy="5" r="1.8" />
      <circle cx="12" cy="12" r="1.8" />
      <circle cx="12" cy="19" r="1.8" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

function CopyIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}
