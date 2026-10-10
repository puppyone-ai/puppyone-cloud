'use client';

import { FileViewer } from '@/lib/viewers/FileViewer';
import { resolveViewer, type PreviewSource } from '@/lib/viewers/resolveViewer';
import type { ViewerProps } from '@/lib/viewers/registry';
import type { WorkspaceResource } from './resourceApi';
import styles from './resource-preview.module.css';

export type ResourceViewModes = Pick<ViewerProps, 'markdownViewMode' | 'csvViewMode' | 'htmlArtifactMode'>;

function viewerInput(resource: WorkspaceResource) {
  let preview: PreviewSource = { kind: 'unavailable' };
  if (resource.preview === 'text' && resource.content_text !== null) preview = { kind: 'text' };
  else if (resource.preview === 'image' && resource.content_base64) {
    preview = { kind: 'image', url: `data:${resource.entry.mime_type};base64,${resource.content_base64}` };
  }
  return { name: resource.entry.name, mimeType: resource.entry.mime_type,
    textContent: resource.content_text ?? undefined, preview, literalText: resource.entry.git_mode === '120000' };
}

export function resourceViewerId(resource: WorkspaceResource) {
  return resolveViewer(viewerInput(resource)).viewerId;
}

/** The resource endpoint supplies bounded bytes; the Workspace owns all rendering. */
export function ResourceContent({ resource, ...modes }: { resource: WorkspaceResource } & ResourceViewModes) {
  const { name, mimeType, ...input } = viewerInput(resource);
  return <div className={styles.content}>
    <div className={styles.viewer}>
      <FileViewer {...input} {...modes} projectId="" filePath={resource.entry.path_bytes_b64 ?? resource.path}
        nodeName={name} mimeType={mimeType ?? undefined} editable={false} />
    </div>
    {resource.truncated && <p className={styles.notice}>This preview is limited. Open Puppyone to view the complete file.</p>}
  </div>;
}
