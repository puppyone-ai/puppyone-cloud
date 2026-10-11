'use client';

import React from 'react';
import { useFilePreviewUrl } from '@/lib/viewers/useFilePreviewUrl';
import { PageLoading } from '@/components/loading';

interface AudioPreviewProps {
  projectId: string;
  filePath: string;
  pathBytes?: string;
  nodeName: string;
  /** Format-resolved MIME (e.g. 'audio/mpeg'). Passed through to
   *  `<source type>` so codec routing is deterministic, in case
   *  the server's blob.type was lost or stripped by a proxy. */
  mimeType?: string;
}

/**
 * Native `<audio>` player backed by a signed inline URL. The browser
 * owns streaming, buffering, and range requests; we don't fetch the
 * whole file into JS memory before playback.
 */
export function AudioPreview({ projectId, filePath, pathBytes, nodeName, mimeType }: AudioPreviewProps) {
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
        }}
      >
        Failed to load audio: {error}
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
        alignItems: 'center',
        justifyContent: 'center',
        padding:
          'var(--po-editor-compact-padding-block) var(--po-editor-compact-padding-inline) var(--po-editor-compact-padding-bottom)',
        boxSizing: 'border-box',
        background: 'var(--po-editor-bg)',
        gap: 16,
      }}
    >
      <div style={{ fontSize: 13, color: 'var(--po-text-muted)' }}>{nodeName}</div>
      <audio key={previewUrl} controls preload="metadata" style={{ width: 'min(560px, 90%)' }}>
        <source src={previewUrl} type={mimeType} />
        Your browser does not support audio playback.
      </audio>
    </div>
  );
}

export default AudioPreview;
