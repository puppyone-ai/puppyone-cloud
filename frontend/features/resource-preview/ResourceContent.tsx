'use client';

import { MarkdownEditor, type MarkdownViewMode } from '@/components/editors/markdown';
import { ImagePreviewSurface } from '@/components/editors/image/ImagePreview';
import { PlainTextEditor } from '@/components/editors/text';
import { BinaryPlaceholder } from '@/components/editors/binary/BinaryPlaceholder';
import type { WorkspaceResource } from './resourceApi';
import styles from './resource-preview.module.css';

export function isMarkdownResource(resource: WorkspaceResource) {
  return /\.(md|markdown|mdown|mdx)$/i.test(resource.entry.name) && resource.entry.git_mode !== '120000';
}

/** Use the Workspace viewers with no mutation handlers or file downloads. */
export function ResourceContent({ resource, markdownViewMode = 'wysiwyg' }: {
  resource: WorkspaceResource; markdownViewMode?: MarkdownViewMode;
}) {
  let content;
  if (resource.preview === 'text' && resource.content_text !== null) {
    content = isMarkdownResource(resource)
      ? <MarkdownEditor content={resource.content_text} documentKey={resource.entry.path_bytes_b64 ?? resource.path}
          readOnly loadImages={false} viewMode={markdownViewMode} />
      : <PlainTextEditor content={resource.content_text} nodeName={resource.entry.name} readOnly />;
  } else if (resource.preview === 'image' && resource.content_base64) {
    content = <ImagePreviewSurface nodeName={resource.entry.name} src={`data:${resource.entry.mime_type};base64,${resource.content_base64}`} />;
  } else content = <BinaryPlaceholder nodeName={resource.entry.name} formatLabel="File" />;
  return <div className={styles.content}>
    <div className={styles.viewer}>{content}</div>
    {resource.truncated && <p className={styles.notice}>This preview is limited. Open Puppyone to view the complete file.</p>}
  </div>;
}
