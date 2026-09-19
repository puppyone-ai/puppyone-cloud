'use client';

import React, { useState, useEffect, useCallback } from 'react';
import styles from './ResizablePanel.module.css';
import type { PaneMotion } from '@/features/workspace/usePaneMotion';

interface ResizablePanelProps {
  children: React.ReactNode;
  className?: string;
  panelRef?: React.Ref<HTMLDivElement>;
  ariaLabel?: string;
  isVisible: boolean;
  defaultWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  topOffset?: number;
  zIndex?: number;
  borderLeftColor?: string;
  background?: string;
  width?: number;
  onWidthChange?: (width: number) => void;
  contentWidth?: number;
  resizable?: boolean;
  motion?: PaneMotion;
  onMotionEnd?: () => void;
  layout?: 'overlay' | 'inline' | 'region' | 'inspector-region';
}

const DEFAULT_WIDTH = 450;
const MIN_WIDTH = 300;
const MAX_WIDTH = 800;

export function ResizablePanel({
  children,
  className,
  panelRef,
  ariaLabel,
  isVisible,
  defaultWidth = DEFAULT_WIDTH,
  minWidth = MIN_WIDTH,
  maxWidth = MAX_WIDTH,
  topOffset = 0,
  zIndex = 20,
  borderLeftColor = 'var(--po-divider)',
  background = 'var(--po-panel)',
  width: controlledWidth,
  onWidthChange,
  contentWidth,
  resizable = true,
  motion,
  onMotionEnd,
  layout = 'overlay',
}: ResizablePanelProps) {
  const [internalWidth, setInternalWidth] = useState(defaultWidth);
  const width = controlledWidth ?? internalWidth;
  const isControlled = controlledWidth !== undefined;
  const setWidth = useCallback((nextWidth: number) => {
    if (!isControlled) setInternalWidth(nextWidth);
    onWidthChange?.(nextWidth);
  }, [isControlled, onWidthChange]);
  const [isResizing, setIsResizing] = useState(false);
  const [isResizeHovered, setIsResizeHovered] = useState(false);
  const [dragStart, setDragStart] = useState<{ startX: number; startWidth: number } | null>(null);
  // React 18 forwards inert as a string attribute; newer React types use boolean.
  const inactiveAttributes: Record<string, string> = isVisible ? {} : { inert: '' };

  useEffect(() => {
    if (!resizable) { setIsResizing(false); setDragStart(null); }
  }, [resizable]);

  useEffect(() => {
    if (!isResizing || !dragStart) return;

    const handleMouseMove = (e: MouseEvent) => {
      const deltaX = dragStart.startX - e.clientX;
      const newWidth = dragStart.startWidth + deltaX;
      setWidth(Math.max(minWidth, Math.min(maxWidth, newWidth)));
    };

    const handleMouseUp = () => {
      setIsResizing(false);
      setDragStart(null);
      setIsResizeHovered(false);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    const previousCursor = document.body.style.cursor;
    const previousSelection = document.body.style.userSelect;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousSelection;
    };
  }, [isResizing, dragStart, minWidth, maxWidth, setWidth]);

  return (
    <div
      className={[styles.panel, className].filter(Boolean).join(' ')}
      ref={panelRef}
      role={ariaLabel ? 'complementary' : undefined}
      aria-label={ariaLabel}
      data-resizable-panel=''
      data-layout={layout}
      data-resizing={isResizing}
      data-visible={isVisible}
      data-motion={motion}
      aria-hidden={!isVisible}
      {...inactiveAttributes}
      onTransitionEnd={event => {
        if (event.target === event.currentTarget && (event.propertyName === 'width' || event.propertyName === 'transform')) onMotionEnd?.();
      }}
      style={{
        '--panel-width': `${width}px`,
        '--panel-top': `${-topOffset}px`,
        '--panel-border': borderLeftColor,
        '--panel-background': background,
        '--panel-z': zIndex,
      } as React.CSSProperties}
    >
      {resizable && <div
        data-panel-resizer=''
        role='separator'
        aria-label='Resize panel'
        aria-orientation='vertical'
        aria-valuemin={minWidth}
        aria-valuemax={maxWidth}
        aria-valuenow={width}
        tabIndex={isVisible ? 0 : -1}
        onKeyDown={event => {
          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
          event.preventDefault();
          setWidth(Math.max(minWidth, Math.min(maxWidth, width + (event.key === 'ArrowLeft' ? 16 : -16))));
        }}
        onMouseDown={e => {
          e.preventDefault();
          e.stopPropagation();
          setDragStart({ startX: e.clientX, startWidth: width });
          setIsResizing(true);
        }}
        onMouseEnter={() => setIsResizeHovered(true)}
        onMouseLeave={() => !isResizing && setIsResizeHovered(false)}
        style={{
          position: 'absolute',
          left: -2,
          top: 0,
          width: 4,
          height: '100%',
          cursor: 'col-resize',
          zIndex: 10,
          background: isResizing || isResizeHovered ? 'var(--po-active)' : 'transparent',
          transition: 'background 0.15s',
        }}
      />}

      <div
        data-panel-content=''
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          overflow: 'hidden',
          width: contentWidth,
          flexShrink: 0,
        }}
      >
        {children}
      </div>
    </div>
  );
}
