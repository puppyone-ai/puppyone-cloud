'use client';

import { VIEWERS, type ViewerProps } from './registry';
import { resolveViewer, type PreviewSource } from './resolveViewer';

/** Shared Workspace/share dispatch. Routing, fetching and saving stay with the host. */
export function FileViewer({ preview, literalText, ...props }: ViewerProps & {
  preview?: PreviewSource; literalText?: boolean;
}) {
  const { format, viewerId } = resolveViewer({ name: props.nodeName, mimeType: props.mimeType,
    textContent: props.textContent, preview, literalText });
  const Viewer = VIEWERS[viewerId].component;
  const editable = !preview && props.editable;
  return <Viewer {...props} editable={editable} onTextChange={editable ? props.onTextChange : undefined}
    monacoLanguage={format.monacoLanguage} formatLabel={format.label}
    inlineImageUrl={preview?.kind === 'image' ? preview.url : undefined}
    loadExternalResources={!preview} />;
}
