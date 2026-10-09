'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { WorkspaceResource } from './resourceApi';
import styles from './resource-preview.module.css';

export function ResourceContent({ resource }: { resource: WorkspaceResource }) {
  const markdown = /\.md(?:own)?$/i.test(resource.path) && resource.entry.git_mode !== '120000';
  let content;
  if (resource.preview === 'text' && resource.content_text !== null) {
    content = (
      markdown ? <article className={styles.markdown}>
        <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
          a: ({ children, href }) => <a href={href} rel="noopener noreferrer">{children}</a>,
          img: ({ alt }) => <span>{alt || 'Image'}</span>,
        }}>{resource.content_text}</ReactMarkdown>
      </article> : <pre>{resource.content_text}</pre>
    );
  } else if (resource.preview === 'image' && resource.content_base64) {
    content = (
      // The server only emits a small allowlist of passive raster image types.
      // eslint-disable-next-line @next/next/no-img-element
      <img className={styles.image} alt={resource.entry.name} src={`data:${resource.entry.mime_type};base64,${resource.content_base64}`} />
    );
  } else content = <div className={styles.empty}>
      <p>This file is available in your workspace.</p>
      <p>Download Puppyone to open this format.</p>
    </div>;
  return <div className={styles.content}>
    {content}
    {resource.truncated && <p className={styles.notice}>This preview is limited. Open Puppyone to view the complete file.</p>}
  </div>;
}
