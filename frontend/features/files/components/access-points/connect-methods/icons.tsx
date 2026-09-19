'use client';

/**
 * SVG icon primitives used inside ConnectMethods.
 * Sizes are inlined per-icon so each one renders at the size its parent
 * expects without overriding from the call site. Strokes use
 * `currentColor` so colours flow from the parent (lets the chevron pick
 * up hover/expanded states from MethodCard's wrapper).
 */

export function CopyIcon() {
  return (
    <svg width={13} height={13} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <rect x={5} y={5} width={8.5} height={8.5} rx={1.5} />
      <path d="M3 10.5V3.5A1.5 1.5 0 0 1 4.5 2H10" />
    </svg>
  );
}

export function CheckIcon() {
  return (
    <svg width={13} height={13} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 8.5l3 3 7-7" />
    </svg>
  );
}

export function EyeIcon() {
  return (
    <svg width={13} height={13} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round">
      <path d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8s-2.5 4.5-6.5 4.5S1.5 8 1.5 8z" />
      <circle cx={8} cy={8} r={2} />
    </svg>
  );
}

export function EyeOffIcon() {
  return (
    <svg width={13} height={13} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 2l12 12" />
      <path d="M3 8s2.5-4.5 6.5-4.5c1.2 0 2.3.4 3.2.9" />
      <path d="M13 11.5c-1.4 1-3 1.5-5 1.5C4 13 1.5 8 1.5 8" />
    </svg>
  );
}

export function TerminalIcon() {
  return (
    <svg width={16} height={16} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 5l3 3-3 3" />
      <path d="M8.5 11h4" />
    </svg>
  );
}

export function SyncIcon() {
  return (
    <svg width={16} height={16} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 8a5 5 0 0 1 8.5-3.5L13 6" />
      <path d="M13 3v3h-3" />
      <path d="M13 8a5 5 0 0 1-8.5 3.5L3 10" />
      <path d="M3 13v-3h3" />
    </svg>
  );
}

export function AgentIcon() {
  return (
    <svg width={16} height={16} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 2.5l1.2 2.6L12 6.3l-2.8 1.2L8 10l-1.2-2.5L4 6.3l2.8-1.2L8 2.5z" />
      <path d="M12.5 11l.6 1.3L14.5 13l-1.4.7-.6 1.3-.6-1.3-1.4-.7 1.4-.7.6-1.3z" />
    </svg>
  );
}

export function Chevron({ expanded }: { readonly expanded: boolean }) {
  return (
    <svg
      width={12}
      height={12}
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{
        transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
        transition: 'transform 0.15s',
        flexShrink: 0,
      }}
      aria-hidden
    >
      <path d="M4 2.5l3.5 3.5L4 9.5" />
    </svg>
  );
}
