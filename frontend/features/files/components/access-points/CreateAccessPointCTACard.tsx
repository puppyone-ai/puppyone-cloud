'use client';

import {
  ACCESS_PANEL_TYPOGRAPHY,
  COLOR_FG_MUTED,
} from '@/features/files/components/access-points/tokens';
import { useState } from 'react';

/**
 * CreateAccessPointCTACard — Overview's entry point to the shared
 * create modal.
 *
 * A single list-shaped primary click target that mirrors the geometry
 * of the scope rows above it (so the Overview reads as a coherent list
 * with a clear "+ new" next step), but its only job is to fire
 * `onCreate()` — the actual create flow lives in the shared
 * CreateAccessModal.
 *
 * Keeping this as an entry card (no form) is deliberate: surfacing
 * create inline made the Overview do double duty (list + create).
 * The sidebar is management/discovery; the modal owns create state.
 */
export function CreateAccessPointCTACard({
  onCreate,
}: {
  readonly onCreate: () => void;
}) {
  const [hovered, setHovered] = useState(false);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* Section label — title case, normal letter spacing per
          2026-05-08 UX feedback ("don't use uppercase + tiny font").
          Reads as a quiet section header, not as a SHOUTED metadata
          tag like the previous design. */}
      <div
        style={{
          ...ACCESS_PANEL_TYPOGRAPHY.title,
          color: COLOR_FG_MUTED,
          padding: '0 2px',
        }}
      >
        Create new access point
      </div>
      <button
        type="button"
        onClick={onCreate}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 11,
          minHeight: 66,
          padding: '9px 11px',
          borderRadius: 8,
          border: '1px dashed var(--po-access-action-border)',
          background: hovered ? 'var(--po-access-active-hover)' : 'var(--po-access-active-bg)',
          color: 'var(--po-access-active-text)',
          cursor: 'pointer',
          textAlign: 'left',
          width: '100%',
          boxShadow: hovered
            ? '0 8px 18px var(--po-access-action-shadow)'
            : 'none',
          transform: hovered ? 'translateY(-1px)' : 'translateY(0)',
          transition:
            'background 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease',
        }}
      >
        <PlusIcon />
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            flex: 1,
            minWidth: 0,
          }}
        >
          <div
            style={{
              ...ACCESS_PANEL_TYPOGRAPHY.title,
              color: 'inherit',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            New access point
          </div>
          <div
            style={{
              marginTop: 3,
              ...ACCESS_PANEL_TYPOGRAPHY.body,
              color: 'color-mix(in srgb, var(--po-access-active-text) 72%, var(--po-text-muted) 28%)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            Choose a folder to share with AI and tools
          </div>
        </div>
        <ChevronRightIcon hovered={hovered} />
      </button>
    </div>
  );
}

function PlusIcon() {
  return (
    <span
      aria-hidden
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 32,
        height: 32,
        borderRadius: 8,
        background: 'color-mix(in srgb, var(--po-access-action) 16%, transparent)',
        color: 'var(--po-access-active-text)',
        flexShrink: 0,
      }}
    >
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path
          d="M8 3V13M3 8H13"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

function ChevronRightIcon({ hovered }: { hovered: boolean }) {
  return (
    <span
      aria-hidden
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 20,
        height: 20,
        color: hovered
          ? 'var(--po-access-active-text)'
          : 'color-mix(in srgb, var(--po-access-active-text) 68%, transparent)',
        transition: 'color 0.15s ease, transform 0.15s ease',
        transform: hovered ? 'translateX(2px)' : 'translateX(0)',
        flexShrink: 0,
      }}
    >
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
        <path
          d="M6 4L10 8L6 12"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}
