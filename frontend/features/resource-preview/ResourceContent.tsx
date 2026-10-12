'use client';

import { FileViewer } from '@/lib/viewers/FileViewer';
import { resolveViewer, type PreviewSource } from '@/lib/viewers/resolveViewer';
import type { ViewerProps } from '@/lib/viewers/registry';
import { isTextLikeCategory, resolveFormat } from '@/lib/fileFormats';
import type { WorkspaceResource } from './resourceApi';
import styles from './resource-preview.module.css';

export type ResourceViewModes = Pick<ViewerProps, 'markdownViewMode' | 'csvViewMode' | 'htmlArtifactMode'>;

function viewerInput(resource: WorkspaceResource) {
  const format = resolveFormat({ name: resource.entry.name, mimeType: resource.entry.mime_type });
  // Text decoding failures and Git links stay literal/unavailable. Native binary
  // viewers load authorized bytes just as they do in the editable Workspace.
  let preview: PreviewSource | undefined = isTextLikeCategory(format) || resource.entry.type === 'gitlink' || resource.entry.git_mode === '120000'
    ? { kind: 'unavailable' } : undefined;
  if (resource.preview === 'text' && resource.content_text !== null) preview = { kind: 'text' };
  else if (resource.preview === 'image' && resource.content_base64) {
    preview = { kind: 'image', url: `data:${resource.entry.mime_type};base64,${resource.content_base64}` };
  }
  // SVG is text on the wire, but uses the same image surface as Workspace.
  if (format.defaultViewer === 'image-preview' && resource.entry.git_mode !== '120000' && preview?.kind === 'text') preview = undefined;
  return { name: resource.entry.name, mimeType: resource.entry.mime_type,
    textContent: resource.content_text ?? undefined, preview, literalText: resource.entry.git_mode === '120000' };
}

export function resourceViewerId(resource: WorkspaceResource) {
  return resolveViewer(viewerInput(resource)).viewerId;
}

/** The resource endpoint owns text reads; the Workspace owns all rendering. */
export function ResourceContent({ resource, projectId, ...modes }: { resource: WorkspaceResource; projectId: string } & ResourceViewModes) {
  const { name, mimeType, ...input } = viewerInput(resource);
  return <div className={styles.content}>
    <div className={styles.viewer}>
      <FileViewer {...input} {...modes} projectId={projectId} filePath={resource.path} pathBytes={resource.entry.path_bytes_b64 ?? undefined}
        nodeName={name} mimeType={mimeType ?? undefined} editable={false} />
    </div>
    {resource.truncated && resource.preview === 'text' && <p className={styles.notice}>This preview is limited. Open Puppyone to view the complete file.</p>}
  </div>;
}
