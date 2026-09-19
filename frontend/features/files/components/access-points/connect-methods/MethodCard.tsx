'use client';

import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import { ProviderIcon } from '@/features/access/components/icons';
import type { MethodMeta } from '@/features/files/components/access-points/connect-methods/meta';
import {
  COLOR_BORDER,
  COLOR_BORDER_HOVER,
  COLOR_FG,
  COLOR_FG_DIM,
  COLOR_FG_MUTED,
} from '@/features/files/components/access-points/tokens';
import {
  isCliProvider,
  isGitRemoteProvider,
} from '@/lib/accessProviderRegistry';
import { useState, type ReactNode } from 'react';

const ACTIVE_METHOD_BG =
  'color-mix(in srgb, var(--po-control) 54%, transparent)';
const ACTIVE_METHOD_BG_HOVER =
  'color-mix(in srgb, var(--po-control) 68%, transparent)';
const PAUSED_METHOD_BG =
  'color-mix(in srgb, var(--po-control) 26%, transparent)';
const PAUSED_METHOD_BG_HOVER =
  'color-mix(in srgb, var(--po-control) 42%, transparent)';

/**
 * MethodCard — wrapper for one connection method.
 *
 * Header layout (left to right):
 *
 *   [icon] [method hint]                               [toggle]
 *
 * The method title lives one level above the card (`Puppyone CLI`,
 * `Git Remote`, etc.). Keeping the title outside the card makes the
 * panel read like normal settings sections instead of mixing section
 * labels into card chrome.
 *
 * Body visibility is **a pure function of the toggle**:
 *
 *   - active  → body expanded
 *   - paused  → body collapsed
 *
 * There is no separate user-controlled expand/collapse affordance —
 * the toggle is the single source of truth. The previous round had a
 * chevron that let the user pin the body open/closed independently of
 * the toggle, which created confusing combinations like
 * "active-but-collapsed" or "paused-but-expanded". The user called
 * this out as redundant: if I turn it off I want it out of the way;
 * if I turn it on I want to see how to use it. The toggle alone now
 * carries both meanings.
 *
 * Other behaviours:
 *
 *   - Toggle is the only interactive element. The header chrome is a
 *     plain `<div>` (not a `<button>`) — clicking on the hint /
 *     icon does nothing, which prevents the "looks
 *     clickable but isn't" trap.
 *   - Toggle is wired to the connector's `status` field via the
 *     `pauseConnector` / `resumeConnector` API (the parent owns the
 *     request; we just emit `onToggle`).
 *   - When the method is paused, the icon and hint dim,
 *     and the hint picks up a "(paused)" suffix so the off state
 *     reads at a glance even without the body for context.
 */
export function MethodCard({
  meta,
  children,
  active = true,
  togglePending = false,
  onToggle,
}: {
  readonly meta: MethodMeta;
  readonly children: ReactNode;
  /** Current connector status — true when `status === 'active'`. When
   *  `false`, the card renders in its paused appearance with the body
   *  hidden. Defaults to `true` when no per-connector state is supplied. */
  readonly active?: boolean;
  /** Set while a pause/resume request is in flight. De-dupes rapid
   *  re-clicks while the first request is still resolving. */
  readonly togglePending?: boolean;
  /** Click handler for the toggle. When omitted, the toggle is not
   *  rendered and the body is always shown. */
  readonly onToggle?: () => void;
}) {
  const [hovered, setHovered] = useState(false);
  // Single source of truth: body visibility = active state. No
  // useState, no manual override.
  const expanded = active;
  const cardBackground = expanded
    ? hovered
      ? ACTIVE_METHOD_BG_HOVER
      : ACTIVE_METHOD_BG
    : hovered
      ? PAUSED_METHOD_BG_HOVER
      : PAUSED_METHOD_BG;

  return (
    <div
      style={{
        borderRadius: 10,
        border: `1px solid ${hovered ? COLOR_BORDER_HOVER : COLOR_BORDER}`,
        background: cardBackground,
        overflow: 'hidden',
        transition: 'border-color 0.15s, background 0.15s',
      }}
    >
      {/* Header is a plain <div>, NOT a button. Mouse hover still
          updates the card border so the user gets a faint "this is a
          live card" cue, but no click handler — only the toggle on
          the right is interactive. */}
      <div
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          width: '100%',
          minHeight: 42,
          padding: '8px 12px',
          color: COLOR_FG,
        }}
      >
        <MethodIcon meta={meta} active={active} />
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: 'flex',
            alignItems: 'center',
          }}
        >
          <span
            style={{
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              fontSize: 12,
              fontWeight: 500,
              color: active ? COLOR_FG_MUTED : COLOR_FG_DIM,
              lineHeight: 1.45,
            }}
          >
            {meta.subtitle}
            {!active && (
              <span style={{ marginLeft: 6, color: COLOR_FG_DIM }}>
                · paused
              </span>
            )}
          </span>
        </div>
        {onToggle && (
          <MethodToggle
            active={active}
            pending={togglePending}
            onClick={onToggle}
            label={`${active ? 'Pause' : 'Resume'} ${meta.title}`}
          />
        )}
      </div>
      {expanded && (
        <div
          style={{
            padding: '12px',
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}

function MethodIcon({
  meta,
  active,
}: {
  readonly meta: MethodMeta;
  readonly active: boolean;
}) {
  const provider = methodProvider(meta.id);
  const tile = getMethodProviderTile(provider, active);
  const tileSize = 26;
  const iconSize = isCliProvider(provider) ? 15 : 14;

  return (
    <div
      style={{
        width: tileSize,
        height: tileSize,
        borderRadius: 6,
        background: tile.background,
        border: `1px solid ${tile.border}`,
        color: tile.color,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        opacity: active ? 1 : 0.62,
        boxShadow: active ? tile.shadow : 'none',
        transition: 'background 0.15s, border-color 0.15s, color 0.15s, opacity 0.15s',
      }}
      aria-hidden
    >
      <ProviderIcon provider={provider} size={iconSize} />
    </div>
  );
}

function methodProvider(id: MethodMeta['id']): string {
  if (id === 'terminal') return 'cli';
  if (id === 'sync') return 'git_remote';
  return 'agent';
}

function getMethodProviderTile(provider: string, active: boolean) {
  if (!active) {
    return {
      background: 'var(--po-control)',
      border: COLOR_BORDER,
      color: COLOR_FG_DIM,
      shadow: 'none',
    };
  }
  if (isCliProvider(provider)) {
    return {
      background: 'var(--po-accent)',
      border: 'var(--po-accent)',
      color: 'var(--po-text-inverse)',
      shadow: '0 1px 2px var(--po-shadow)',
    };
  }
  if (isGitRemoteProvider(provider)) {
    return {
      background: 'var(--po-text-inverse)',
      border: COLOR_BORDER_HOVER,
      color: COLOR_FG_MUTED,
      shadow: '0 1px 2px color-mix(in srgb, var(--po-shadow) 70%, transparent)',
    };
  }
  return {
    background: 'color-mix(in srgb, var(--po-control) 58%, transparent)',
    border: COLOR_BORDER_HOVER,
    color: COLOR_FG_MUTED,
    shadow: 'none',
  };
}

/**
 * MethodToggle — compact on/off switch for a method card.
 *
 * 32×18 track, 14×14 thumb. Active state uses a single restrained
 * success colour, while paused falls back to neutral grey.
 *
 * Optimistic UI contract: the parent flips `active` immediately on
 * click — the actual pause / resume API call runs fire-and-forget in
 * the background. The `pending` prop deduplicates concurrent requests
 * and replaces the switch visual with the product's official compact
 * loading animation while the server catches up.
 *
 * Note on event propagation: the parent header is now a plain <div>
 * (not a <button>), so click bubbling no longer triggers a card-level
 * toggle. The `stopPropagation` is kept defensively in case the
 * surrounding chrome ever wires up a click handler — it's cheap and
 * correctly scopes the click to the switch itself.
 */
function MethodToggle({
  active,
  pending,
  onClick,
  label,
}: {
  readonly active: boolean;
  readonly pending: boolean;
  readonly onClick: () => void;
  readonly label: string;
}) {
  return (
    <ToggleSwitch
      as="span"
      checked={active}
      pending={pending}
      ariaLabel={pending ? 'Saving connector state' : label}
      title={pending ? 'Saving' : label}
      size="sm"
      stopPropagation
      onCheckedChange={onClick}
    />
  );
}

/**
 * SectionHeader — the `Connect` eyebrow above the method stack.
 */
export function SectionHeader({
  eyebrow,
  description,
}: {
  readonly eyebrow: string;
  readonly description?: string;
}) {
  return (
    <div style={{ padding: '0 2px' }}>
      <div
        style={{
          fontSize: 12,
          fontWeight: 500,
          color: COLOR_FG_DIM,
        }}
      >
        {eyebrow}
      </div>
      {description && (
        <div style={{ fontSize: 12, color: COLOR_FG_DIM, marginTop: 4, lineHeight: 1.45 }}>
          {description}
        </div>
      )}
    </div>
  );
}

/**
 * NoAccessKeyNotice — compatibility banner for a redacted CLI key.
 * An empty scope.access_key does not prove that no credential exists;
 * ordinary reads intentionally never return plaintext.
 */
export function NoAccessKeyNotice() {
  return (
    <div
      style={{
        borderRadius: 8,
        border: '1px solid color-mix(in srgb, var(--po-warning) 28%, transparent)',
        background: 'color-mix(in srgb, var(--po-warning) 8%, transparent)',
        color: 'var(--po-warning)',
        fontSize: 12,
        lineHeight: 1.5,
        padding: '10px 12px',
      }}
    >
      CLI key plaintext is hidden after issuance. Generate a new one from scope settings when you need setup commands.
    </div>
  );
}
