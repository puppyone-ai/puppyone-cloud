'use client';

import React from 'react';
import { useFilePreviewUrl } from '@/lib/viewers/useFilePreviewUrl';
import { PageLoading } from '@/components/loading';

interface PdfPreviewProps {
  projectId: string;
  filePath: string;
  pathBytes?: string;
  nodeName: string;
}

/**
 * PDF preview using the browser's built-in PDF viewer via a signed
 * inline URL. Zero-dependency — every modern browser ships a PDF.js
 * implementation and exposes it through `<iframe src="..." />`.
 *
 * If we ever need annotation tooling or page extraction, swap this
 * for `react-pdf` and bump the package — the registry entry doesn't
 * have to change.
 */
export function PdfPreview({ projectId, filePath, pathBytes, nodeName }: PdfPreviewProps) {
  const { url: previewUrl, error } = useFilePreviewUrl(projectId, filePath, 'inline', pathBytes);

  if (error) {
    return (
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--po-danger)',
          padding:
            'var(--po-editor-compact-padding-block) var(--po-editor-compact-padding-inline) var(--po-editor-compact-padding-bottom)',
          boxSizing: 'border-box',
        }}
      >
        Failed to load PDF: {error}
      </div>
    );
  }

  if (!previewUrl) {
    return <PageLoading variant="fill" />;
  }

  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        padding: 16,
        boxSizing: 'border-box',
        background: 'var(--po-editor-bg)',
      }}
    >
      <iframe
        src={previewUrl}
        title={nodeName}
        style={{
          flex: 1,
          width: '100%',
          border: '1px solid var(--po-border-subtle)',
          borderRadius: 6,
          background: 'var(--po-panel-raised)',
        }}
      />
    </div>
  );
}

export default PdfPreview;
