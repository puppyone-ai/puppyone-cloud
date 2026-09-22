'use client';

import { useRef } from 'react';
import type { CSSProperties, MouseEvent, PointerEvent, ReactNode } from 'react';
import { ActivityIconButton } from '@/components/ActivityIconButton';
import { APP_Z_INDEX } from '@/lib/zIndex';
import { ModalPortal } from './ModalPortal';

type DialogLayer = 'modal' | 'modalNested';
type DialogBackdrop = 'default' | 'strong' | 'none';

type DialogRootProps = {
  open?: boolean;
  onClose?: () => void;
  children: ReactNode;
  layer?: DialogLayer;
  backdrop?: DialogBackdrop;
  dismissOnBackdrop?: boolean;
  style?: CSSProperties;
};

function backdropColor(backdrop: DialogBackdrop) {
  if (backdrop === 'none') return 'transparent';
  if (backdrop === 'strong') return 'var(--po-backdrop-strong)';
  return 'var(--po-backdrop)';
}

export function DialogRoot({
  open = true,
  onClose,
  children,
  layer = 'modal',
  backdrop = 'default',
  dismissOnBackdrop = true,
  style,
}: DialogRootProps) {
  const pointerStartedOnBackdropRef = useRef(false);

  if (!open) return null;

  const handleBackdropPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    pointerStartedOnBackdropRef.current = event.target === event.currentTarget;
  };

  const handleBackdropClick = (event: MouseEvent<HTMLDivElement>) => {
    const clickedBackdrop = event.target === event.currentTarget;
    if (dismissOnBackdrop && pointerStartedOnBackdropRef.current && clickedBackdrop) {
      onClose?.();
    }
    pointerStartedOnBackdropRef.current = false;
  };

  return (
    <ModalPortal>
      <div
        data-dialog-root=""
        role="presentation"
        onPointerDown={handleBackdropPointerDown}
        onClick={handleBackdropClick}
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: APP_Z_INDEX[layer],
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 16,
          background: backdropColor(backdrop),
          backdropFilter: backdrop === 'none' ? undefined : 'blur(2px)',
          WebkitBackdropFilter: backdrop === 'none' ? undefined : 'blur(2px)',
          boxSizing: 'border-box',
          ...style,
        }}
      >
        {children}
      </div>
    </ModalPortal>
  );
}

type DialogSurfaceProps = {
  children: ReactNode;
  width?: number | string;
  maxWidth?: number | string;
  maxHeight?: number | string;
  ariaLabel?: string;
  variant?: 'project-settings';
  ariaLabelledBy?: string;
  style?: CSSProperties;
};

export function DialogSurface({
  children,
  width = 420,
  maxWidth = 'calc(100vw - 32px)',
  maxHeight = 'calc(100vh - 32px)',
  ariaLabel,
  variant,
  ariaLabelledBy,
  style,
}: DialogSurfaceProps) {
  const stopPropagation = (event: MouseEvent<HTMLDivElement>) => {
    event.stopPropagation();
  };

  return (
    <div
      data-dialog-surface={variant ?? 'default'}
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
      onClick={stopPropagation}
      style={{
        width,
        maxWidth,
        maxHeight,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--po-overlay)',
        border: '1px solid var(--po-border)',
        borderRadius: 12,
        color: 'var(--po-text)',
        boxShadow: '0 24px 48px var(--po-shadow)',
        boxSizing: 'border-box',
        animation: 'dialog-fade-in 0.16s ease-out',
        ...style,
      }}
    >
      <style>{`
        @keyframes dialog-fade-in {
          from { opacity: 0; transform: scale(0.985); }
          to { opacity: 1; transform: scale(1); }
        }
      `}</style>
      {children}
    </div>
  );
}

type DialogHeaderProps = {
  title: ReactNode;
  description?: ReactNode;
  leading?: ReactNode;
  onClose?: () => void;
  closeTitle?: string;
  children?: ReactNode;
  style?: CSSProperties;
};

export function DialogHeader({
  title,
  description,
  leading,
  onClose,
  closeTitle = 'Close',
  children,
  style,
}: DialogHeaderProps) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        gap: 16,
        padding: '14px 20px 8px',
        flexShrink: 0,
        ...style,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: description ? 'flex-start' : 'center',
          gap: 10,
          minWidth: 0,
        }}
      >
        {leading && <div style={{ flexShrink: 0, marginTop: description ? 1 : 0 }}>{leading}</div>}
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 500,
              lineHeight: '18px',
              color: 'var(--po-text-muted)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {title}
          </div>
          {description && (
            <div
              style={{
                marginTop: 4,
                fontSize: 13,
                lineHeight: '18px',
                color: 'var(--po-text-muted)',
              }}
            >
              {description}
            </div>
          )}
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
        {children}
        {onClose && (
          <ActivityIconButton kind="close" title={closeTitle} onClick={onClose} />
        )}
      </div>
    </div>
  );
}

type DialogBodyProps = {
  children: ReactNode;
  style?: CSSProperties;
};

export function DialogBody({ children, style }: DialogBodyProps) {
  return (
    <div data-dialog-body=''
      style={{
        padding: '12px 24px 20px',
        overflow: 'auto',
        color: 'var(--po-text-muted)',
        ...style,
      }}
    >
      {children}
    </div>
  );
}

type DialogFooterProps = {
  children: ReactNode;
  justify?: CSSProperties['justifyContent'];
  style?: CSSProperties;
};

export function DialogFooter({
  children,
  justify = 'flex-end',
  style,
}: DialogFooterProps) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: justify,
        gap: 10,
        padding: '0 20px 20px',
        background: 'var(--po-overlay)',
        flexShrink: 0,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
