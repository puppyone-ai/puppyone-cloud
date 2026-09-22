"use client";

import { useEffect, useState, type ReactNode } from "react";
import { FilePreviewIcon } from "../../file/fileIcons";
import type { EditorViewerContext } from "../viewerTypes";

export function ImageResourceViewer({ document, content, fileUrl, fileUrlLoading, fileUrlError }: EditorViewerContext) {
  const imageSource = fileUrl || content || document.preview || null;
  return (
    <ResourcePreviewState fileUrl={imageSource} loading={fileUrlLoading} error={fileUrlError} label="image">
      {(url) => (
        <div className="native-preview native-preview-centered">
          <img className="native-image-preview" src={url} alt={document.name} />
        </div>
      )}
    </ResourcePreviewState>
  );
}

export function PdfResourceViewer({ document, fileUrl, fileUrlLoading, fileUrlError }: EditorViewerContext) {
  return (
    <ResourcePreviewState fileUrl={fileUrl} loading={fileUrlLoading} error={fileUrlError} label="PDF">
      {(url) => (
        <div className="native-preview native-preview-framed">
          <PdfPreviewFrame url={url} title={document.name} />
        </div>
      )}
    </ResourcePreviewState>
  );
}

export function AudioResourceViewer({ document, fileUrl, fileUrlLoading, fileUrlError, fileIconTheme }: EditorViewerContext) {
  return (
    <ResourcePreviewState fileUrl={fileUrl} loading={fileUrlLoading} error={fileUrlError} label="audio">
      {(url) => (
        <div className="native-preview native-preview-centered">
          <div className="native-media-card">
            <FilePreviewIcon name={document.name} type="audio" size={54} theme={fileIconTheme} />
            <strong>{document.name}</strong>
            <audio controls preload="metadata">
              <source src={url} type={document.mimeType ?? undefined} />
              Your browser does not support audio playback.
            </audio>
          </div>
        </div>
      )}
    </ResourcePreviewState>
  );
}

export function VideoResourceViewer({ document, fileUrl, fileUrlLoading, fileUrlError }: EditorViewerContext) {
  return (
    <ResourcePreviewState fileUrl={fileUrl} loading={fileUrlLoading} error={fileUrlError} label="video">
      {(url) => (
        <div className="native-preview native-preview-centered">
          <video className="native-video-preview" controls preload="metadata">
            <source src={url} type={document.mimeType ?? undefined} />
            Your browser does not support video playback.
          </video>
        </div>
      )}
    </ResourcePreviewState>
  );
}

function ResourcePreviewState({
  fileUrl,
  loading,
  error,
  label,
  children,
}: {
  fileUrl?: string | null;
  loading: boolean;
  error?: string | null;
  label: string;
  children: (fileUrl: string) => ReactNode;
}) {
  if (error) return <div className="editor-state danger">Failed to load {label}: {error}</div>;
  if (loading && !fileUrl) return <div className="editor-state">Loading preview...</div>;
  if (!fileUrl) return <div className="editor-state">No preview available for this {label}.</div>;
  return <>{children(fileUrl)}</>;
}

function PdfPreviewFrame({ url, title }: { url: string; title: string }) {
  const shouldUseBlobUrl = url.startsWith("puppyone-local:");
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [blobError, setBlobError] = useState<string | null>(null);

  useEffect(() => {
    if (!shouldUseBlobUrl) {
      setBlobUrl(null);
      setBlobError(null);
      return undefined;
    }

    let cancelled = false;
    let objectUrl: string | null = null;

    setBlobUrl(null);
    setBlobError(null);

    fetch(url)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        return response.blob();
      })
      .then((blob) => {
        const pdfBlob = blob.type === "application/pdf"
          ? blob
          : blob.slice(0, blob.size, "application/pdf");
        objectUrl = URL.createObjectURL(pdfBlob);
        if (cancelled) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        setBlobUrl(objectUrl);
      })
      .catch((error) => {
        if (!cancelled) setBlobError(error instanceof Error ? error.message : String(error));
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [shouldUseBlobUrl, url]);

  if (!shouldUseBlobUrl) {
    return <iframe className="native-preview-frame" src={url} title={title} />;
  }

  if (blobError) {
    return <div className="editor-state danger">Failed to load PDF: {blobError}</div>;
  }

  if (!blobUrl) {
    return <div className="editor-state">Loading preview...</div>;
  }

  return <iframe className="native-preview-frame" src={blobUrl} title={title} />;
}
