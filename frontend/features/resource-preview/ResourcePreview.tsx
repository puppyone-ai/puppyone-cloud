'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import useSWR from 'swr';
import { Files, Link2, Download, LockKeyhole } from 'lucide-react';
import { workspaceResourcePath, workspaceByteResourcePath, workspaceEntryPath } from '@puppyone/cloud-core';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import { useWorkspaceRouter } from '@/features/workspace/navigation';
import { ProjectHeaderFrame } from '@/components/project/ProjectHeaderFrame';
import { ProjectHeaderBreadcrumbs } from '@/components/project/ProjectHeaderBreadcrumbs';
import { DesktopChromeAction } from '@/components/chrome/DesktopChrome';
import { WorkspaceFileColumn } from '@/components/sidebar/WorkspaceFileColumn';
import { FileNavigationRegion } from '@/components/sidebar/FileNavigationRegion';
import { WorkspaceFilesButton } from '@/components/sidebar/WorkspaceFilesButton';
import { ResponsiveWorkspaceProvider } from '@/features/workspace/responsive';
import { WorkspaceLayoutFrame } from '@/features/workspace/WorkspaceLayoutFrame';
import { createWorkspaceLayoutStore } from '@/features/workspace/layoutStore';
import { DEFAULT_WORKSPACE_INPUT } from '@/features/workspace/paneLayout';
import { useWorkspaceRegions } from '@/features/workspace/regions';
import { DataNoFileSelectedState } from '@/features/files/components/DataNoFileSelectedState';
import { EditorLoadingSurface } from '@/components/loading';
import { ReadErrorState } from '@/components/loading/ReadErrorState';
import { FileViewerModePicker } from '@/features/files/components/FileViewerHeaderActions';
import { getProject } from '@/lib/projectsApi';
import { ResourceContent, resourceViewerId, type ResourceViewModes } from './ResourceContent';
import type { WorkspaceResource } from './resourceApi';
import { ResourceExplorer } from './ResourceExplorer';
import { useResource, defaultPreview } from './resourceQueries';
import styles from './resource-preview.module.css';

const DOWNLOAD_URL = 'https://www.puppyone.ai/en/download';
type ResourceLocation = { projectId: string; path: string; pathBytes?: string };

export function ResourcePreview(props: ResourceLocation) {
  const { isAuthReady, session, authError } = useAuth();
  const [layout] = useState(() => createWorkspaceLayoutStore({ projects: { ...DEFAULT_WORKSPACE_INPUT.projects, present: false } }));
  const returnPath = props.pathBytes ? workspaceByteResourcePath(props.projectId, props.pathBytes) : workspaceResourcePath(props.projectId, props.path);
  return <ResponsiveWorkspaceProvider layoutStore={layout}><WorkspaceLayoutFrame>
    <main className="workspace-project-layout">
      {isAuthReady && session ? <ResourceView key={`${session.user.id}:${props.projectId}`} {...props} userId={session.user.id} /> : <>
        <ProjectHeaderFrame actions={<DownloadLink />}><span className={styles.brand}>Puppyone</span></ProjectHeaderFrame>
        {!isAuthReady ? <div className={styles.empty} role="status">{authError || 'Loading workspace…'}</div> :
          <section className={styles.empty}>
            <LockKeyhole size={24} aria-hidden="true" /><h1>Sign in to view this workspace</h1>
            <p>Use an account that has access to this workspace.</p>
            <Link className={styles.signIn} href={`/login?${new URLSearchParams({ redirect: returnPath })}`}>Sign in</Link>
          </section>}
      </>}
    </main>
  </WorkspaceLayoutFrame></ResponsiveWorkspaceProvider>;
}

function ResourceView({ projectId, path, userId, pathBytes }: ResourceLocation & { userId: string }) {
  const router = useWorkspaceRouter();
  const regions = useWorkspaceRegions();
  const { data, error, isLoading, mutate } = useResource(userId, projectId, path, pathBytes);
  const first = data?.kind === 'directory' ? defaultPreview(data.entries) : undefined;
  const firstFile = useResource(userId, projectId, first?.path ?? null, first?.path_bytes_b64 ?? undefined);
  const displayed = data?.kind === 'file' ? data : firstFile.data;
  const previewError = error || firstFile.error;
  const { data: project } = useSWR(['resource-project', userId, projectId], () => getProject(projectId),
    { revalidateOnFocus: false, shouldRetryOnError: false });
  const [copyResult, setCopyResult] = useState({ path, message: '' });
  const [modes, setModes] = useState<Required<ResourceViewModes>>({ markdownViewMode: 'wysiwyg', csvViewMode: 'preview', htmlArtifactMode: 'preview' });
  const segments = useMemo(() => [
    { label: project?.name || 'Workspace', href: path || pathBytes ? workspaceResourcePath(projectId) : undefined },
    ...path.split('/').filter(Boolean).map((name, index, parts) => ({ label: name,
      href: index < parts.length - 1 ? workspaceResourcePath(projectId, parts.slice(0, index + 1).join('/'), true) : undefined })),
    ...(pathBytes && data ? [{ label: data.entry.name }] : []),
  ], [project?.name, projectId, path, pathBytes, data]);
  useEffect(() => {
    if (!data) return;
    const canonical = workspaceEntryPath(projectId, data.entry);
    if (window.location.pathname + window.location.search !== canonical) router.replace(canonical, { scroll: false });
  }, [data, projectId, router]);
  const copy = async () => {
    try {
      const locator = data ? workspaceEntryPath(projectId, data.entry) : window.location.pathname + window.location.search;
      await navigator.clipboard.writeText(window.location.origin + locator);
      setCopyResult({ path, message: 'Link copied.' });
    } catch { setCopyResult({ path, message: 'Could not copy. Copy the address from your browser.' }); }
  };
  return <>
    <ProjectHeaderFrame headerRef={regions.header} actions={<div className={styles.actions}>
      {displayed && <FileViewerModePicker viewerId={resourceViewerId(displayed)} editable={false}
        markdownViewMode={modes.markdownViewMode} onMarkdownViewModeChange={mode => setModes(value => ({ ...value, markdownViewMode: mode }))}
        csvViewMode={modes.csvViewMode} onCsvViewModeChange={mode => setModes(value => ({ ...value, csvViewMode: mode }))}
        editorType="monaco" onEditorTypeChange={() => {}}
        htmlMode={modes.htmlArtifactMode} onHtmlModeChange={mode => setModes(value => ({ ...value, htmlArtifactMode: mode }))} />}
      <DesktopChromeAction icon={<Link2 size={15} />} label="Copy link" onClick={() => void copy()} />
      <DownloadLink />
      <span className="sr-only" role="status">{copyResult.path === path ? copyResult.message : ''}</span>
    </div>}>
      <WorkspaceFilesButton />
      <ProjectHeaderBreadcrumbs pathSegments={segments} />
      <span className={styles.divider} aria-hidden="true" />
      <span className={styles.filesTab}><Files size={15} strokeWidth={1.8} aria-hidden="true" />Files</span>
    </ProjectHeaderFrame>
    <div className={`workspace-primary-body ${styles.body}`} ref={regions.projectBody}>
      <FileNavigationRegion><WorkspaceFileColumn>
        <ResourceExplorer userId={userId} projectId={projectId} currentPath={path} selected={displayed?.entry} />
      </WorkspaceFileColumn></FileNavigationRegion>
      <section ref={regions.editor} className={`workspace-data-content ${styles.preview}`} aria-label="File preview">
        <PreviewPane projectId={projectId} loading={isLoading || firstFile.isLoading} error={previewError} displayed={displayed} directory={data}
          modes={modes} retry={() => { void (error ? mutate() : firstFile.mutate()); }} />
      </section>
    </div>
  </>;
}

function PreviewPane({ projectId, loading, error, displayed, directory, modes, retry }: {
  projectId: string;
  loading: boolean; error?: { status?: number }; displayed?: WorkspaceResource; directory?: WorkspaceResource;
  modes: ResourceViewModes; retry: () => void;
}) {
  if (loading) return <EditorLoadingSurface />;
  if (error) return <ReadErrorState label={error.status === 403 || error.status === 404 ? 'This resource is not available.' : 'Could not load this preview.'} onRetry={retry} />;
  if (displayed) return <ResourceContent key={displayed.entry.path_bytes_b64 ?? displayed.path} projectId={projectId} resource={displayed} {...modes} />;
  return <DataNoFileSelectedState mode={directory?.entries.length ? 'has-content' : 'empty-folder'} folderName={directory?.entry.name} />;
}

function DownloadLink() {
  return <a className={styles.download} href={DOWNLOAD_URL} aria-label="Download Puppyone" title="Download Puppyone">
    <Download size={15} aria-hidden="true" /><span data-chrome-label>Download Puppyone</span>
  </a>;
}
