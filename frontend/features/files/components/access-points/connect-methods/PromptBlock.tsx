'use client';

import { CheckIcon, CopyIcon } from '@/features/files/components/access-points/connect-methods/icons';
import {
  COLOR_BORDER,
  COLOR_FG_MUTED,
  FONT_MONO,
} from '@/features/files/components/access-points/tokens';
import { useCallback, useState } from 'react';

const PROMPT_PREVIEW_BG = 'var(--po-panel)';

/**
 * PromptBlock — the headline action of every connection method.
 *
 * Prompt text is shown in a compact code box with a bottom-edge copy
 * action. The button is visible as the block's primary action, but it
 * does not float in the middle of the preview and steal the whole
 * surface's hierarchy.
 */
export function PromptBlock({
  prompt,
}: {
  readonly prompt: string;
}) {
  const [copied, setCopied] = useState(false);
  const [hovered, setHovered] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }, [prompt]);

  return (
    <div
      style={{
        position: 'relative',
        height: 132,
        borderRadius: 8,
        border: `1px solid ${COLOR_BORDER}`,
        background: PROMPT_PREVIEW_BG,
        overflow: 'hidden',
      }}
    >
      <pre
        style={{
          margin: 0,
          padding: '12px 14px 58px 14px',
          fontFamily: FONT_MONO,
          fontSize: 10,
          lineHeight: 1.6,
          color: COLOR_FG_MUTED,
          opacity: 0.82,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
        aria-hidden
      >
        {prompt}
      </pre>
      <div
        aria-hidden
        style={{
          position: 'absolute',
          inset: 'auto 0 0 0',
          height: 68,
          background: `linear-gradient(180deg, transparent 0%, ${PROMPT_PREVIEW_BG} 100%)`,
          pointerEvents: 'none',
          zIndex: 1,
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: '50%',
          bottom: 10,
          transform: 'translateX(-50%)',
          zIndex: 2,
        }}
      >
        <button
          type="button"
          onClick={handleCopy}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 7,
            height: 30,
            padding: '0 12px',
            fontSize: 12,
            fontWeight: 600,
            fontFamily: 'inherit',
            lineHeight: 1,
            color: copied ? 'var(--po-success-contrast)' : 'var(--po-success)',
            background: copied
              ? 'var(--po-success)'
              : hovered
                ? 'color-mix(in srgb, var(--po-success) 20%, var(--po-panel) 80%)'
                : 'color-mix(in srgb, var(--po-success) 14%, var(--po-panel) 86%)',
            border: copied
              ? '1px solid var(--po-success)'
              : '1px solid color-mix(in srgb, var(--po-success) 38%, transparent)',
            borderRadius: 6,
            cursor: 'pointer',
            whiteSpace: 'nowrap',
            boxShadow: 'none',
            transition: 'background 0.12s ease, border-color 0.12s ease, color 0.12s ease',
          }}
        >
          <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 13, height: 13 }}>
            {copied ? <CheckIcon /> : <CopyIcon />}
          </span>
          {copied ? 'Copied' : 'Copy setup prompt'}
        </button>
      </div>
    </div>
  );
}
