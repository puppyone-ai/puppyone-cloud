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
  "style-src 'unsafe-inline' https:",
  "font-src data: https:",
  "script-src 'none'",
  "connect-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join('; ');

function buildSandboxedDocument(rawHtml: string, loadExternalResources: boolean): string {
  const policy = loadExternalResources ? HTML_PREVIEW_CSP : HTML_PREVIEW_CSP.replaceAll(" https:", "");
  const document = new DOMParser().parseFromString(rawHtml, 'text/html');
  // Policy must precede every resource, including malformed/fragment HTML.
  // Document-provided refresh/base elements cannot navigate or change resolution.
  document.querySelectorAll('base, meta[http-equiv]').forEach(node => node.remove());
  const csp = document.createElement('meta');
  csp.httpEquiv = 'Content-Security-Policy';
  csp.content = policy;
  const referrer = document.createElement('meta');
  referrer.name = 'referrer';
  referrer.content = 'no-referrer';
  const base = document.createElement('base');
  base.target = '_blank';
  document.head.prepend(csp, referrer, base);
  return `<!doctype html>${document.documentElement.outerHTML}`;
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
          referrerPolicy="no-referrer"
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
