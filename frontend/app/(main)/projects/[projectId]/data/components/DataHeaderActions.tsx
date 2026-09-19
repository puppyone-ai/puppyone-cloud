'use client';

import { useEffect, useRef, useState } from 'react';
import type { MouseEvent as ReactMouseEvent, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import {
  Copy,
  Download,
  EllipsisVertical,
  Pencil,
  Trash2,
} from 'lucide-react';
import { DesktopChromeAction } from '@/components/chrome/DesktopChrome';
import { APP_Z_INDEX } from '@/lib/zIndex';

export type DataHeaderActionTarget = {
  id: string;
  name: string;
  type: string;
  isFolder: boolean;
  isRoot: boolean;
  isSynced?: boolean;
};

type DataHeaderActionsProps = {
  target: DataHeaderActionTarget;
  onRename: (id: string, currentName: string) => void;
  onDelete: (id: string, name: string) => void;
  onDownload: (id: string, name: string) => void;
};

export function DataHeaderActions({
  target,
  onRename,
  onDelete,
  onDownload,
}: DataHeaderActionsProps) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const updatePosition = () => {
    const button = buttonRef.current;
    if (!button) return;
    const rect = button.getBoundingClientRect();
    const menuWidth = 188;
    const left = Math.max(8, Math.min(window.innerWidth - menuWidth - 8, rect.right - menuWidth));
    setPos({ top: rect.bottom + 4, left });
  };

  useEffect(() => {
    if (!open) return;
    updatePosition();

    const onDocClick = (event: MouseEvent) => {
      const targetNode = event.target as Node;
      if (buttonRef.current?.contains(targetNode) || menuRef.current?.contains(targetNode)) return;
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

  const handleCopyPath = async () => {
    setOpen(false);
    try {
      await navigator.clipboard.writeText(target.id || '/');
    } catch (error) {
      console.error('[DataHeaderActions] Failed to copy path:', error);
    }
  };

  if (target.isFolder || target.isRoot) return null;

  return (
    <>
      <DesktopChromeAction
        ref={buttonRef}
        active={open}
        icon={<EllipsisVertical size={15} strokeWidth={2} />}
        label='Data actions'
        title="Data actions"
        aria-label="Data actions"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          if (!open) updatePosition();
          setOpen((value) => !value);
        }}
      />

      {open && pos && typeof document !== 'undefined' && createPortal(
        <div
          ref={menuRef}
          role="menu"
          style={{
            position: 'fixed',
            top: pos.top,
            left: pos.left,
            minWidth: 188,
            padding: 4,
            background: 'var(--po-panel-raised)',
            border: '1px solid var(--po-border)',
            borderRadius: 6,
            boxShadow: '0 14px 34px var(--po-shadow)',
            zIndex: APP_Z_INDEX.popover,
          }}
        >
          {!target.isRoot && !target.isSynced && (
            <ActionMenuItem
              label="Rename"
              icon={<Pencil size={14} />}
              onClick={() => {
                setOpen(false);
                onRename(target.id, target.name);
              }}
            />
          )}

          {!target.isRoot && (
            <ActionMenuItem
              label="Download"
              icon={<Download size={14} />}
              onClick={() => {
                setOpen(false);
                onDownload(target.id, target.name);
              }}
            />
          )}

          <ActionMenuItem
            label="Copy path"
            icon={<Copy size={14} />}
            onClick={handleCopyPath}
          />

          {!target.isRoot && <MenuDivider />}

          {!target.isRoot && (
            <ActionMenuItem
              label="Delete"
              icon={<Trash2 size={14} />}
              destructive
              onClick={() => {
                setOpen(false);
                onDelete(target.id, target.name);
              }}
            />
          )}
        </div>,
        document.body,
      )}
    </>
  );
}

function MenuDivider() {
  return (
    <div
      role="separator"
      style={{
        height: 1,
        margin: '4px 6px',
        background: 'var(--po-divider)',
      }}
    />
  );
}

function ActionMenuItem({
  icon,
  label,
  onClick,
  destructive,
}: {
  icon: ReactNode;
  label: string;
  onClick: (event: ReactMouseEvent<Element>) => void;
  destructive?: boolean;
}) {
  const [hovered, setHovered] = useState(false);
  const color = destructive ? 'var(--po-danger)' : 'var(--po-text-muted)';
  const hoverBg = destructive ? 'color-mix(in srgb, var(--po-danger) 10%, transparent)' : 'var(--po-hover)';

  return (
    <button
      type="button"
      role="menuitem"
      onClick={(event) => {
        event.stopPropagation();
        onClick(event);
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
        background: hovered ? hoverBg : 'transparent',
        border: 'none',
        borderRadius: 4,
        color,
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
          color,
          flexShrink: 0,
        }}
      >
        {icon}
      </span>
      <span style={{ flex: 1, whiteSpace: 'nowrap' }}>{label}</span>
    </button>
  );
}
