'use client';

import { CheckIcon, CopyIcon } from '@/features/files/components/access-points/connect-methods/icons';
import { COLOR_FG, COLOR_FG_DIM, COLOR_SUCCESS } from '@/features/files/components/access-points/tokens';
import { useState, type ReactNode } from 'react';

export function IconButton({
  label,
  onClick,
  children,
}: {
  readonly label: string;
  readonly onClick: () => void;
  readonly children: ReactNode;
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      title={label}
      aria-label={label}
      style={{
        width: 24,
        height: 24,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: hovered ? 'var(--po-border-subtle)' : 'transparent',
        border: 'none',
        borderRadius: 6,
        color: hovered ? COLOR_FG : COLOR_FG_DIM,
        cursor: 'pointer',
        transition: 'background 0.12s, color 0.12s',
      }}
    >
      {children}
    </button>
  );
}

export function CopyIconButton({ text }: { readonly text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Ignore — clipboard can fail in restricted contexts.
    }
  };
  return (
    <IconButton label={copied ? 'Copied' : 'Copy'} onClick={handleCopy}>
      <span style={{ color: copied ? COLOR_SUCCESS : 'inherit', display: 'flex' }}>
        {copied ? <CheckIcon /> : <CopyIcon />}
      </span>
    </IconButton>
  );
}
