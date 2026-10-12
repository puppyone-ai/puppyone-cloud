'use client';

import React from 'react';
import { useFilePreviewUrl } from '@/components/editors/useFilePreviewUrl';
import { PageLoading } from '@/components/loading';

interface ImagePreviewProps {
  projectId: string;
  filePath: string;
  pathBytes?: string;
  nodeName: string;
}

/**
 * Renders any `category: 'image'` file via blob URL — works for
 * PNG/JPG/GIF/WebP/AVIF/SVG/BMP/ICO. HEIC is registry-routed to
 * `binary-placeholder` because no major browser can decode it.
 */
export function ImagePreview({ projectId, filePath, pathBytes, nodeName }: ImagePreviewProps) {
  const { url: blobUrl, error } = useFilePreviewUrl(projectId, filePath, 'blob', pathBytes);

  if (error) {
    return (
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--po-danger)',
        }}
      >
        Failed to load image: {error}
      </div>
    );
  }

  if (!blobUrl) {
    return <PageLoading variant="fill" />;
  }

  return <ImagePreviewSurface src={blobUrl} nodeName={nodeName} />;
}

/** Image surface shared by full files and bounded share previews. */
export function ImagePreviewSurface({ src, nodeName }: { src: string; nodeName: string }) {
  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        overflow: 'auto',
        padding:
          'var(--po-editor-compact-padding-block) var(--po-editor-compact-padding-inline) var(--po-editor-compact-padding-bottom)',
        boxSizing: 'border-box',
        background: 'var(--po-editor-bg)',
      }}
    >
      <div style={{ fontSize: 13, color: 'var(--po-text-subtle)', marginBottom: 12 }}>{nodeName}</div>
      <img
        src={src}
        alt={nodeName}
        style={{
          maxWidth: '100%',
          maxHeight: 'calc(100% - 40px)',
          objectFit: 'contain',
          borderRadius: 8,
        }}
      />
    </div>
  );
}

export default ImagePreview;
