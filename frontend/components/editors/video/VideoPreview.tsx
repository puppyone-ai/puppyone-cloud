'use client';

import React from 'react';
import { useFilePreviewUrl } from '@/lib/viewers/useFilePreviewUrl';
import { PageLoading } from '@/components/loading';

interface VideoPreviewProps {
  projectId: string;
  filePath: string;
  pathBytes?: string;
  nodeName: string;
  /** Format-resolved MIME (e.g. 'video/mp4'). See AudioPreview note. */
  mimeType?: string;
}

/**
 * Native `<video>` player backed by a signed inline URL. The browser
 * owns streaming, buffering, and range requests; we don't fetch the
 * whole file into JS memory before playback.
 */
export function VideoPreview({ projectId, filePath, pathBytes, nodeName, mimeType }: VideoPreviewProps) {
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
        Failed to load video: {error}
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
        gap: 12,
      }}
    >
      <div style={{ fontSize: 13, color: 'var(--po-text-muted)' }}>{nodeName}</div>
      <video
        key={previewUrl}
        controls
        preload="metadata"
        style={{
          maxWidth: '100%',
          maxHeight: 'calc(100% - 40px)',
          borderRadius: 8,
          background: 'var(--po-inset)',
        }}
      >
        <source src={previewUrl} type={mimeType} />
        Your browser does not support video playback.
      </video>
    </div>
  );
}

export default VideoPreview;
