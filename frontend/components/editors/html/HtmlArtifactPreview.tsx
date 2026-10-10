'use client';

import { useEffect, useMemo, useState } from 'react';
import dynamic from 'next/dynamic';
import { EditorLoadingSurface } from '@/components/loading';

const MonacoCodeViewer = dynamic(
  () => import('@/components/editors/code/MonacoCodeViewer').then((mod) => mod.MonacoCodeViewer),
  { ssr: false, loading: () => <EditorLoadingSurface /> },
);

interface HtmlArtifactPreviewProps {
  content: string;
  nodeName: string;
  mode: HtmlArtifactMode;
  loadExternalResources?: boolean;
}

export type HtmlArtifactMode = 'preview' | 'source';

const HTML_PREVIEW_CSP = [
  "default-src 'none'",
  "img-src data: blob: https:",
  "style-src 'unsafe-inline'",
  "font-src data: https:",
  "script-src 'none'",
  "connect-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join('; ');

function buildSandboxedDocument(rawHtml: string, loadExternalResources: boolean): string {
  const policy = loadExternalResources ? HTML_PREVIEW_CSP : HTML_PREVIEW_CSP.replaceAll(" https:", "");
  const csp = `<meta http-equiv="Content-Security-Policy" content="${policy}">`;
  const base = '<base target="_blank">';

  if (/<head[\s>]/i.test(rawHtml)) {
    return rawHtml.replace(/<head([^>]*)>/i, `<head$1>${csp}${base}`);
  }

  if (/<html[\s>]/i.test(rawHtml)) {
    return rawHtml.replace(/<html([^>]*)>/i, `<html$1><head>${csp}${base}</head>`);
  }

  return `<!doctype html><html><head>${csp}${base}</head><body>${rawHtml}</body></html>`;
}

export function HtmlArtifactPreview({ content, nodeName, mode, loadExternalResources = true }: HtmlArtifactPreviewProps) {
  const [sourceMounted, setSourceMounted] = useState(false);
  const sandboxedDocument = useMemo(() => buildSandboxedDocument(content, loadExternalResources), [content, loadExternalResources]);

  useEffect(() => {
    if (mode === 'source') setSourceMounted(true);
  }, [mode]);

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--po-editor-bg)',
      }}
    >
      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        <iframe
          title={nodeName || 'HTML artifact preview'}
          sandbox="allow-popups"
          srcDoc={sandboxedDocument}
          style={{
            flex: 1,
            width: '100%',
            minHeight: 0,
            border: 0,
            background: 'var(--po-panel-raised)',
            display: mode === 'preview' ? 'block' : 'none',
          }}
        />
        {sourceMounted && (
          <div
            style={{
              flex: 1,
              minHeight: 0,
              display: mode === 'source' ? 'flex' : 'none',
              background: 'var(--po-editor-bg)',
            }}
          >
            <MonacoCodeViewer content={content} language="html" readOnly />
          </div>
        )}
      </div>
    </div>
  );
}

export default HtmlArtifactPreview;
