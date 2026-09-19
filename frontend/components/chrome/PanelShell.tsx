'use client';

import React from 'react';
import { ActivityIconButton } from '@/components/ActivityIconButton';

interface PanelShellProps {
  /**
   * Title row content. Accepts any ReactNode so callers can compose
   * rich titles (e.g. "Access  5" with a muted count badge inline at
   * the same font size) without working around a flat string. When a
   * plain string is passed it renders as before. The string variant
   * is also used as the `title` attribute for tooltip purposes; rich
   * titles can supply that themselves via a wrapper if they need it.
   */
  title: React.ReactNode;
  /**
   * Optional secondary line rendered under the title in muted small type.
   * Used for inline meta (path / mode / status) so the panel's body
   * doesn't have to repeat scope/identity info. Capped to 46px header
   * height — title + subtitle stack vertically inside the same row.
   */
  subtitle?: string;
  icon?: React.ReactNode;
  onClose: () => void;
  onBack?: () => void;
  headerRight?: React.ReactNode;
  /** Render only the body. Used when the page-level header owns the
   *  panel chrome (Access side sheet in the data page). */
  hideHeader?: boolean;
  children: React.ReactNode;
}

export function PanelShell({ title, subtitle, icon, onClose, onBack, headerRight, hideHeader = false, children }: PanelShellProps) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {!hideHeader && (
        <div style={{
          height: 46, minHeight: 46, display: 'flex', alignItems: 'center', gap: 8,
          padding: '0 12px',
          background: 'var(--po-canvas)',
          flexShrink: 0,
        }}>
          {/* Back / close use the shared ActivityIconButton chrome so the
              panel header reads as one consistent affordance family with
              the floating activity widgets and other panels (per
              2026-05-08 UX feedback: don't ship 3 different icon-button
              visuals across the chrome). */}
          {onBack && (
            <ActivityIconButton kind="back" title="Back" onClick={onBack} />
          )}
          {icon && <span style={{ display: 'flex', alignItems: 'center', flexShrink: 0 }}>{icon}</span>}
          <div
            style={{
              flex: 1,
              minWidth: 0,
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'center',
              gap: 1,
            }}
          >
            <div
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: 'var(--po-text)',
                lineHeight: '18px',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
              // `title` attr only when caller passes a plain string —
              // ReactNode titles compose their own tooltip semantics if
              // they need any.
              title={typeof title === 'string' ? title : undefined}
            >
              {title}
            </div>
            {subtitle && (
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 400,
                  color: 'var(--po-text-subtle)',
                  lineHeight: '14px',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
                title={subtitle}
              >
                {subtitle}
              </div>
            )}
          </div>
          {headerRight}
          <ActivityIconButton kind="close" title="Close panel" onClick={onClose} />
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {children}
      </div>
    </div>
  );
}
