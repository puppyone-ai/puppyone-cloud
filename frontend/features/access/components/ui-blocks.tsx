'use client';

/**
 * Shared atomic UI building blocks for the access page.
 *
 * Each one is small (15-90 lines) and gets used in 2+ places across
 * ConnectorCard, ScopeDetailPanel, and the various Quick-Connect Body
 * components. Co-locating them in a single file makes it cheap for an
 * AI / human reader to scan "what neutral primitives can I reach for?"
 * before reinventing one — splitting them per-file would force 9
 * imports for the same idea.
 *
 * Family overview:
 *  - Buttons / badges       : GhostButton, PermBadge
 *  - Section labels         : SectionLabel, SubSectionLabel
 *  - Notice                 : NoAccessKeyNotice
 *  - Copy-paste UX          : PromptBlock, CommandBlock, KvBlock, KvRow
 *
 * NONE of these talk to the network or hold business state — they're
 * pure presentation. Anything stateful (SWR, mutations) belongs in
 * `hooks/` or in the parent feature component.
 */

import React, { useCallback, useState } from 'react';
import { AiHandoffButton } from '@/components/ui/AiHandoffButton';
import {
  T,
  BTN_RADIUS,
  PROMPT_BLOCK_HEIGHT,
  PROMPT_BG,
} from '@/features/access/lib/tokens';
import { CopyIcon } from '@/features/access/components/icons';

const PROMPT_PREVIEW_BG = PROMPT_BG;

// ─── Buttons & badges ────────────────────────────────────────────────
//
// Two button sizes only. Section-level ghost actions (Edit Scope, View
// all, Copy connect) all share `GhostButton`; primary actions on the
// Identity row (Pause/Resume, More) share `PrimaryGhostButton`. Both
// pull from the same neutral token palette on hover, so we no longer
// have three different sizes / fonts
// / colors competing for attention on the same screen.

export function GhostButton({
  icon,
  children,
  onClick,
  variant = 'default',
  disabled = false,
  title,
  ariaLabel,
}: {
  readonly icon?: React.ReactNode;
  readonly children?: React.ReactNode;
  readonly onClick?: () => void;
  readonly variant?: 'default' | 'square';
  readonly disabled?: boolean;
  readonly title?: string;
  readonly ariaLabel?: string;
}) {
  const isSquare = variant === 'square';
  const baseColor = T.text2;
  const hoverColor = T.text1;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={ariaLabel}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 6,
        height: 30,
        width: isSquare ? 30 : undefined,
        padding: isSquare ? 0 : '0 12px',
        background: 'transparent',
        border: `1px solid ${T.border}`,
        borderRadius: BTN_RADIUS,
        color: baseColor,
        fontSize: 12,
        fontWeight: 500,
        fontFamily: T.fontSans,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: `background 0.15s ${T.ease}, color 0.15s ${T.ease}, border-color 0.15s ${T.ease}`,
      }}
      onMouseEnter={(e) => {
        if (disabled) return;
        e.currentTarget.style.background = 'var(--po-hover)';
        e.currentTarget.style.borderColor = 'var(--po-border-strong)';
        e.currentTarget.style.color = hoverColor;
      }}
      onMouseLeave={(e) => {
        if (disabled) return;
        e.currentTarget.style.background = 'transparent';
        e.currentTarget.style.borderColor = T.border;
        e.currentTarget.style.color = baseColor;
      }}
    >
      {icon}
      {children}
    </button>
  );
}

export function PermBadge({ label, active }: { readonly label: string; readonly active: boolean }) {
  // Read/write badges are neutral on purpose. The Scope card already
  // tells the user *what* is bound; the badges are just a yes/no
  // signal on capability. Coloring them per-provider would be more
  // chrome for no extra information.
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: 22,
        padding: '0 8px',
        borderRadius: 5,
        background: active ? 'var(--po-border-subtle)' : 'transparent',
        border: `1px solid ${active ? 'var(--po-border-strong)' : T.border}`,
        color: active ? T.text2 : T.text4,
        fontSize: 10,
        fontWeight: 500,
        fontFamily: T.fontSans,
        letterSpacing: '0.02em',
      }}
    >
      {label}
    </span>
  );
}

// ─── Layout primitives ───────────────────────────────────────────────
//
// Two label tiers, by design — both Title Case, distinguished only by
// size and weight. ALL-CAPS is gone from the access page entirely:
//
//   • SectionLabel    — page-level section (sibling to other top-level
//     blocks). 14px / 600 / T.text2. Reads as a heading.
//     Used for "Scope", "Settings", "Connectors".
//
//   • SubSectionLabel — card-internal eyebrow (a small disambiguator
//     inside an already-bounded surface). 10px / 600 / T.text3.
//     Visually distinct from the page-level label by being smaller +
//     dimmer, not by being uppercase. Used for "Configuration",
//     "Prompt for AI agent", "Recent activity", etc.
//
// Acronyms ("CLI", "MCP", "API", "OAuth", "ID") that read naturally as
// caps are still rendered exactly as the source string says — that's
// orthography, not styling, and we don't normalize it.

export function SectionLabel({ children, right }: { readonly children: React.ReactNode; readonly right?: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, paddingLeft: 2 }}>
      <span
        style={{
          fontSize: 14,
          fontWeight: 600,
          color: T.text2,
          fontFamily: T.fontSans,
          letterSpacing: '-0.005em',
        }}
      >
        {children}
      </span>
      {right}
    </div>
  );
}

export function SubSectionLabel({ children, right }: { readonly children: React.ReactNode; readonly right?: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6, paddingLeft: 2 }}>
      <span
        style={{
          fontSize: 10,
          fontWeight: 600,
          color: T.text3,
          fontFamily: T.fontSans,
        }}
      >
        {children}
      </span>
      {right}
    </div>
  );
}

// ─── Notice ──────────────────────────────────────────────────────────

export function NoAccessKeyNotice() {
  return (
    <div
      style={{
        marginBottom: 10,
        borderRadius: 6,
        border: `1px solid color-mix(in srgb, var(--po-warning) 25%, transparent)`,
        background: 'color-mix(in srgb, var(--po-warning) 6%, transparent)',
        color: 'var(--po-warning)',
        fontSize: 12,
        lineHeight: 1.5,
        padding: '8px 10px',
      }}
    >
      CLI key plaintext is hidden after issuance. Generate a new one from scope settings when you need setup commands.
    </div>
  );
}

// ─── Prompt block ────────────────────────────────────────────────────

export function PromptBlock({ prompt }: { readonly prompt: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async (event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable — silent */
    }
  }, [prompt]);

  return (
    <div
      style={{
        position: 'relative',
        minHeight: PROMPT_BLOCK_HEIGHT,
        borderRadius: 7,
        border: `1px solid ${T.cardBorder}`,
        background: 'color-mix(in srgb, var(--po-inset) 92%, var(--po-panel) 8%)',
        overflow: 'hidden',
        marginBottom: 0,
      }}
    >
      <pre
        aria-hidden
        style={{
          margin: 0,
          padding: '10px 12px 42px',
          fontFamily: T.fontMono,
          fontSize: 12,
          lineHeight: 1.55,
          color: 'color-mix(in srgb, var(--po-text) 58%, var(--po-text-muted) 42%)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          maxHeight: PROMPT_BLOCK_HEIGHT,
          overflow: 'hidden',
        }}
      >
        {prompt || 'Access setup is preparing.'}
      </pre>
      <div
        aria-hidden
        style={{
          position: 'absolute',
          inset: 0,
          background: 'linear-gradient(180deg, color-mix(in srgb, var(--po-inset) 18%, transparent) 0%, color-mix(in srgb, var(--po-inset) 92%, var(--po-panel) 8%) 82%)',
          pointerEvents: 'none',
        }}
      />
      <AiHandoffButton
        disabled={!prompt}
        onClick={handleCopy}
        copied={copied}
        style={{
          position: 'absolute',
          right: 18,
          top: '50%',
          transform: 'translateY(-50%)',
        }}
      />
    </div>
  );
}

// ─── Command block ───────────────────────────────────────────────────

export function CommandBlock({ lines }: { readonly lines: readonly string[] }) {
  const text = lines.join('\n');
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard unavailable — silent */
    }
  }, [text]);

  return (
    <div
      style={{
        borderRadius: 5,
        border: `1px solid ${T.cardBorder}`,
        background: PROMPT_BG,
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <pre
        style={{
          margin: 0,
          padding: '10px 40px 10px 12px',
          fontFamily: T.fontMono,
          fontSize: 12,
          lineHeight: 1.6,
          color: T.text1,
          overflowX: 'auto',
          whiteSpace: 'pre',
        }}
      >
        {text}
      </pre>
      <button
        type="button"
        onClick={handleCopy}
        title={copied ? 'Copied' : 'Copy'}
        aria-label={copied ? 'Copied' : 'Copy command'}
        style={{
          all: 'unset',
          position: 'absolute',
          top: 6,
          right: 6,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 24,
          height: 24,
          borderRadius: 5,
          color: copied ? 'var(--po-success)' : T.text2,
          cursor: 'pointer',
          transition: `color 0.12s ${T.ease}, background 0.12s ${T.ease}`,
        }}
        onMouseEnter={(e) => {
          if (!copied) e.currentTarget.style.color = T.text1;
          e.currentTarget.style.background = 'var(--po-border-subtle)';
        }}
        onMouseLeave={(e) => {
          if (!copied) e.currentTarget.style.color = T.text2;
          e.currentTarget.style.background = 'transparent';
        }}
      >
        <CopyIcon size={12} />
      </button>
    </div>
  );
}

// ─── KV blocks ───────────────────────────────────────────────────────

export function KvBlock({
  rows,
}: {
  readonly rows: ReadonlyArray<{ label: string; value: string; mono?: boolean; copyable?: boolean }>;
}) {
  return (
    <div
      style={{
        marginBottom: 14,
        background: PROMPT_BG,
        border: `1px solid ${T.cardBorder}`,
        borderRadius: 6,
        overflow: 'hidden',
      }}
    >
      {rows.map((row, idx) => (
        <KvRow key={row.label} row={row} isFirst={idx === 0} />
      ))}
    </div>
  );
}

export function KvRow({
  row,
  isFirst,
}: {
  readonly row: { label: string; value: string; mono?: boolean; copyable?: boolean };
  readonly isFirst: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(async () => {
    if (!row.value) return;
    try {
      await navigator.clipboard.writeText(row.value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard unavailable — silent */
    }
  }, [row.value]);

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '8px 10px',
        borderTop: isFirst ? 'none' : `1px solid ${T.cardBorder}`,
      }}
    >
      <span
        style={{
          width: 96,
          flexShrink: 0,
          fontSize: 10,
          fontWeight: 500,
          color: T.text3,
          fontFamily: T.fontSans,
        }}
      >
        {row.label}
      </span>
      <span
        style={{
          flex: 1,
          minWidth: 0,
          fontSize: 12,
          color: T.text2,
          fontFamily: row.mono ? T.fontMono : T.fontSans,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {row.value || '—'}
      </span>
      {row.copyable && (
        <button
          type="button"
          onClick={handleCopy}
          aria-label={copied ? 'Copied' : 'Copy'}
          title={copied ? 'Copied' : 'Copy'}
          style={{
            all: 'unset',
            cursor: 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 24,
            height: 24,
            borderRadius: 5,
            color: copied ? 'var(--po-success)' : T.text2,
            transition: `color 0.12s ${T.ease}, background 0.12s ${T.ease}`,
          }}
          onMouseEnter={(e) => {
            if (!copied) e.currentTarget.style.color = T.text1;
            e.currentTarget.style.background = 'var(--po-border-subtle)';
          }}
          onMouseLeave={(e) => {
            if (!copied) e.currentTarget.style.color = T.text2;
            e.currentTarget.style.background = 'transparent';
          }}
        >
          <CopyIcon size={12} />
        </button>
      )}
    </div>
  );
}
