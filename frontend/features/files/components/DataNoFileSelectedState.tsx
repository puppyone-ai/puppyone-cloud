'use client';

import { BUTTON_HEIGHT } from '@/components/ui/buttonTokens';
import { FileText, Upload } from 'lucide-react';
import type { ReactNode } from 'react';

export type DataNoFileSelectedMode =
  | 'has-content'
  | 'empty-workspace'
  | 'empty-folder';

interface DataNoFileSelectedStateProps {
  readonly mode: DataNoFileSelectedMode;
  readonly folderName?: string;
  readonly onCreateMarkdown: () => void | Promise<void>;
  readonly onUploadClick: () => void;
}

export function DataNoFileSelectedState({
  mode,
  folderName,
  onCreateMarkdown,
  onUploadClick,
}: DataNoFileSelectedStateProps) {
  const isEmpty = mode !== 'has-content';
  const title =
    mode === 'empty-workspace'
      ? 'This workspace is empty'
      : mode === 'empty-folder'
        ? 'This folder is empty'
        : 'No file selected';
  const description =
    mode === 'empty-workspace'
      ? 'Create a Markdown note or upload files to add context.'
      : mode === 'empty-folder'
        ? `${folderName ? `${folderName} is empty. ` : ''}Create a Markdown note or upload files here.`
        : 'Choose a file from the sidebar to open it here.';

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 32,
        background: 'var(--po-canvas)',
      }}
    >
      <div
        style={{
          width: isEmpty ? 'min(420px, 100%)' : 'min(300px, 100%)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          textAlign: 'center',
        }}
      >
        <div
          aria-hidden
          style={{
            width: isEmpty ? 34 : 28,
            height: isEmpty ? 34 : 28,
            borderRadius: isEmpty ? 8 : 7,
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            marginBottom: isEmpty ? 12 : 10,
            color: isEmpty ? 'var(--po-text-muted)' : 'var(--po-text-subtle)',
            background: isEmpty
              ? 'color-mix(in srgb, var(--po-control) 42%, transparent)'
              : 'transparent',
            border: `1px solid ${isEmpty ? 'var(--po-border-subtle)' : 'transparent'}`,
          }}
        >
          <FileText size={isEmpty ? 16 : 15} strokeWidth={1.75} />
        </div>

        <h2
          style={{
            margin: 0,
            fontSize: isEmpty ? 15 : 14,
            lineHeight: isEmpty ? '22px' : '20px',
            fontWeight: isEmpty ? 600 : 500,
            letterSpacing: 0,
            color: isEmpty ? 'var(--po-text)' : 'var(--po-text-muted)',
          }}
        >
          {title}
        </h2>
        <p
          style={{
            margin: isEmpty ? '6px 0 0' : '4px 0 0',
            maxWidth: isEmpty ? 380 : 280,
            fontSize: 12,
            lineHeight: '18px',
            fontWeight: 400,
            letterSpacing: 0,
            color: 'var(--po-text-subtle)',
          }}
        >
          {description}
        </p>

        {isEmpty && (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
              gap: 10,
              width: 'min(320px, 100%)',
              marginTop: 18,
            }}
          >
            <EmptyActionButton
              icon={<FileText size={14} strokeWidth={1.9} />}
              label="New Markdown"
              onClick={() => {
                void onCreateMarkdown();
              }}
            />
            <EmptyActionButton
              icon={<Upload size={14} strokeWidth={1.9} />}
              label="Upload files"
              onClick={onUploadClick}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyActionButton({
  icon,
  label,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        height: BUTTON_HEIGHT,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        minWidth: 0,
        padding: '0 12px',
        borderRadius: 7,
        border: '1px solid var(--po-border-strong)',
        background: 'color-mix(in srgb, var(--po-panel) 30%, transparent)',
        color: 'var(--po-text)',
        fontSize: 12,
        fontWeight: 600,
        cursor: 'pointer',
      }}
    >
      {icon}
      <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {label}
      </span>
    </button>
  );
}
