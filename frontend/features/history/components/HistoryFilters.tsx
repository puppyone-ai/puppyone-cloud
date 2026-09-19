'use client';

import { StatusDot } from '@/components/ui/StatusDot';
import { type ReactNode } from 'react';

export function HistoryFilterGroup({
  label,
  children,
}: {
  readonly label: string;
  readonly children: ReactNode;
}) {
  return (
    <div style={{ minWidth: 0, padding: '4px 0' }}>
      <div
        style={{
          padding: '4px 8px 5px',
          color: 'var(--po-text-disabled)',
          fontSize: 10,
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: '0.04em',
        }}
      >
        {label}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {children}
      </div>
    </div>
  );
}

export function HistoryFilterOption({
  selected,
  label,
  count,
  markerColor,
  showMarkerSlot = false,
  onClick,
}: {
  readonly selected: boolean;
  readonly label: string;
  readonly count: number;
  readonly markerColor?: string;
  readonly showMarkerSlot?: boolean;
  readonly onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={selected}
      onClick={onClick}
      className={`w-full min-w-0 rounded-md px-2 text-left transition-colors ${
        selected
          ? 'bg-[var(--po-selected)] text-[var(--po-text)]'
          : 'text-[var(--po-text-muted)] hover:bg-[var(--po-hover)] hover:text-[var(--po-text)]'
      }`}
      style={{
        height: 28,
        display: 'flex',
        alignItems: 'center',
        gap: 7,
        fontSize: 12,
        fontWeight: 500,
      }}
    >
      {showMarkerSlot ? (
        <StatusDot style={{ background: markerColor ?? 'transparent' }} />
      ) : null}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <span
        style={{
          color: 'var(--po-text-disabled)',
          fontSize: 10,
          fontWeight: 500,
          flexShrink: 0,
        }}
      >
        {count}
      </span>
      {selected ? (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden style={{ flexShrink: 0 }}>
          <path d="M20 6 9 17l-5-5" />
        </svg>
      ) : null}
    </button>
  );
}
