'use client';

import { CountBadge } from '@/components/ui/CountBadge';
import { COLOR_FG, COLOR_FG_DIM } from '@/features/files/components/access-points/tokens';
import type { ReactNode } from 'react';

export function NumberedStep({
  number,
  title,
  hint,
  children,
}: {
  readonly number: number;
  readonly title: string;
  readonly hint?: string;
  readonly children: ReactNode;
}) {
  return (
    <div style={{ display: 'flex', gap: 10 }}>
      <span style={{ marginTop: 2 }}>
        <CountBadge value={number} size="sm" tone="neutral" ariaHidden />
      </span>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: COLOR_FG, lineHeight: 1.4 }}>
          {title}
        </div>
        {hint && (
          <div style={{ fontSize: 10, color: COLOR_FG_DIM, lineHeight: 1.5 }}>{hint}</div>
        )}
        {children}
      </div>
    </div>
  );
}
