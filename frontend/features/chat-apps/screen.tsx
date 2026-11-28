'use client';

import type { CSSProperties, ReactNode } from 'react';

/** Shared centred layout for the chat-apps landing pages (same look as the invite page). */
export function Screen({ children }: { children: ReactNode }) {
  return (
    <div style={container}>
      <img src="/puppyone-logo.svg" alt="Puppyone" width={48} height={48} />
      {children}
    </div>
  );
}

export function Message({ children, strong }: { children: ReactNode; strong?: boolean }) {
  return <div style={strong ? { ...muted, color: 'var(--po-text)', fontSize: 15 } : muted} role="status">{children}</div>;
}

export function Button({ children, onClick, primary }: { children: ReactNode; onClick: () => void; primary?: boolean }) {
  return <button type="button" onClick={onClick} style={primary ? primaryButton : secondaryButton}>{children}</button>;
}

const container: CSSProperties = {
  minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center',
  justifyContent: 'center', gap: 20, background: 'var(--po-inset)', padding: 24, textAlign: 'center',
};
const muted: CSSProperties = { fontSize: 13, color: 'var(--po-text-muted)', maxWidth: 340 };
const base: CSSProperties = { height: 40, padding: '0 16px', borderRadius: 6, fontSize: 14, cursor: 'pointer' };
const primaryButton: CSSProperties = {
  ...base, border: 'none', background: 'var(--po-text)', color: 'var(--po-text-inverse)', fontWeight: 600,
};
const secondaryButton: CSSProperties = {
  ...base, border: '1px solid var(--po-border)', background: 'var(--po-control)', color: 'var(--po-text)', fontWeight: 500,
};
