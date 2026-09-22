"use client";

import { Code2, Eye } from "lucide-react";
import { useState } from "react";
import { getHtmlPreviewInteractionCss } from "../htmlPreviewInteraction";
import { PlainTextEditor } from "../PlainTextEditor";
import type { EditorViewerContext, MarkdownHtmlTrustMode } from "../viewerTypes";

export function HtmlViewer({
  document,
  content,
  fileUrl,
  fileUrlLoading,
  fileUrlError,
  loading,
  error,
  htmlTrustMode,
}: EditorViewerContext) {
  const [mode, setMode] = useState<"preview" | "source">("preview");

  if (loading && !content && !fileUrl) return <div className="editor-state">Loading HTML...</div>;
  if (error && !content && !fileUrl) return <div className="editor-state danger">{error}</div>;
  if (content && fileUrlLoading && !fileUrl) return <div className="editor-state">Loading preview...</div>;
  if (fileUrlLoading && !content && !fileUrl) return <div className="editor-state">Loading preview...</div>;
  if (fileUrlError && !content && !fileUrl) {
    return <div className="editor-state danger">Failed to load HTML: {fileUrlError}</div>;
  }

  const sourceAvailable = Boolean(content);
  const resolvedMode = sourceAvailable ? mode : "preview";

  return (
    <section className="html-preview-shell" data-mode={resolvedMode}>
      <div className="html-preview-toolbar" aria-label="HTML view mode">
        <button
          className={resolvedMode === "preview" ? "active" : ""}
          type="button"
          title="HTML preview"
          aria-label="HTML preview"
          onClick={() => setMode("preview")}
        >
          <Eye size={14} strokeWidth={2} />
        </button>
        <button
          className={resolvedMode === "source" ? "active" : ""}
          type="button"
          title={sourceAvailable ? "HTML source" : "HTML source unavailable"}
          aria-label="HTML source"
          disabled={!sourceAvailable}
          onClick={() => setMode("source")}
        >
          <Code2 size={14} strokeWidth={2} />
        </button>
      </div>

      {resolvedMode === "source" ? (
        <div className="html-source-preview">
          <PlainTextEditor content={content} nodeName={document.name} readOnly />
        </div>
      ) : (
        <div className="native-preview native-preview-framed">
          <HtmlPreviewFrame
            path={document.path}
            title={document.name}
            content={content || null}
            fileUrl={fileUrl}
            htmlTrustMode={htmlTrustMode}
          />
        </div>
      )}
    </section>
  );
}

function HtmlPreviewFrame({
  path,
  title,
  content,
  fileUrl,
  htmlTrustMode,
}: {
  path: string;
  title: string;
  content?: string | null;
  fileUrl?: string | null;
  htmlTrustMode: MarkdownHtmlTrustMode;
}) {
  const policy = getHtmlPreviewPolicy(htmlTrustMode);
  const useFileUrl = htmlTrustMode === "localTrusted"
    ? Boolean(fileUrl)
    : Boolean(fileUrl && !content);
  const frameKey = [
    path,
    htmlTrustMode,
    fileUrl ?? "",
    content ? `${content.length}:${hashString(content)}` : "",
  ].join("|");

  return (
    <iframe
      key={frameKey}
      className="native-preview-frame"
      data-html-trust-mode={htmlTrustMode}
      title={title}
      sandbox={policy.sandbox}
      referrerPolicy="no-referrer"
      src={useFileUrl ? fileUrl ?? undefined : undefined}
      srcDoc={!useFileUrl && content ? buildHtmlPreviewDocument(content, fileUrl, policy) : undefined}
    />
  );
}

type HtmlPreviewPolicy = {
  sandbox: string;
  csp: string | null;
};

const SAFE_HTML_PREVIEW_CSP = [
  "default-src 'none'",
  "img-src data: blob: https: puppyone-local:",
  "media-src data: blob: https: puppyone-local:",
  "style-src 'unsafe-inline' https: puppyone-local:",
  "font-src data: https: puppyone-local:",
  "script-src 'none'",
  "connect-src 'none'",
  "object-src 'none'",
  "frame-src 'none'",
  "child-src 'none'",
  "base-uri https: puppyone-local:",
  "form-action 'none'",
].join("; ");

function getHtmlPreviewPolicy(htmlTrustMode: MarkdownHtmlTrustMode): HtmlPreviewPolicy {
  if (htmlTrustMode === "localTrusted") {
    return {
      sandbox: "allow-downloads allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-scripts",
      csp: null,
    };
  }

  return {
    sandbox: "allow-popups allow-popups-to-escape-sandbox",
    csp: SAFE_HTML_PREVIEW_CSP,
  };
}

function buildHtmlPreviewDocument(rawHtml: string, baseHref: string | null | undefined, policy: HtmlPreviewPolicy): string {
  const csp = policy.csp
    ? `<meta http-equiv="Content-Security-Policy" content="${escapeHtmlAttribute(policy.csp)}">`
    : "";
  const base = baseHref
    ? `<base href="${escapeHtmlAttribute(baseHref)}" target="_blank">`
    : '<base target="_blank">';
  const interactionStyle = `<style id="puppyone-html-preview-interaction">${getHtmlPreviewInteractionCss("body")}</style>`;

  if (/<head[\s>]/i.test(rawHtml)) {
    return rawHtml.replace(/<head([^>]*)>/i, `<head$1>${csp}${base}${interactionStyle}`);
  }

  if (/<html[\s>]/i.test(rawHtml)) {
    return rawHtml.replace(/<html([^>]*)>/i, `<html$1><head>${csp}${base}${interactionStyle}</head>`);
  }

  return `<!doctype html><html><head>${csp}${base}${interactionStyle}</head><body>${rawHtml}</body></html>`;
}

function escapeHtmlAttribute(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function hashString(value: string): string {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (Math.imul(31, hash) + value.charCodeAt(index)) | 0;
  }
  return String(hash >>> 0);
}
