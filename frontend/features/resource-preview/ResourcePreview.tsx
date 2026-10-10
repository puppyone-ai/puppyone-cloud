'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useWorkspaceRouter } from '@/features/workspace/navigation';
import useSWR, { unstable_serialize, useSWRConfig } from 'swr';
import { File, Folder, Link2, Download, ChevronRight, LockKeyhole, Cloud } from 'lucide-react';
import { workspaceResourcePath, workspaceByteResourcePath, workspaceEntryPath } from '@puppyone/cloud-core';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import { readWorkspaceResource, type WorkspaceResource, type ResourceEntry } from './resourceApi';
import { ResourceContent } from './ResourceContent';
import styles from './resource-preview.module.css';

const DOWNLOAD_URL = 'https://www.puppyone.ai/en/download';
type ResourceLocation = { projectId: string; path: string; pathBytes?: string };
const resourceKey = (user: string, project: string, path: string, bytes?: string, cursor?: string) =>
  ['workspace-resource', user, project, path, bytes ?? '', cursor ?? ''];

export function ResourcePreview({ projectId, path, pathBytes }: ResourceLocation) {
  const { isAuthReady, session, authError } = useAuth();
  const returnPath = pathBytes ? workspaceByteResourcePath(projectId, pathBytes) : workspaceResourcePath(projectId, path);
  let content;
  if (!isAuthReady) content = <p className={styles.loading} role="status">{authError || 'Loading workspace…'}</p>;
  else if (!session) content = <section className={styles.empty}>
    <LockKeyhole size={28} aria-hidden="true" /><h1>Sign in to view this workspace</h1>
    <p>Use an account that has access to this workspace.</p>
    <Link className={styles.primary} href={`/login?${new URLSearchParams({ redirect: returnPath })}`}>Sign in</Link>
  </section>;
  else content = <ResourceView key={`${session.user.id}:${projectId}`} projectId={projectId} path={path} pathBytes={pathBytes} userId={session.user.id} />;
  return <main className={styles.page}>
    <header className={styles.header}>
      <Link href="/home" className={styles.brand}>Puppyone</Link>
      <span className={styles.headerContext}><Cloud size={15} aria-hidden="true" /> Shared workspace</span>
      <a className={styles.download} href={DOWNLOAD_URL}><Download size={15} aria-hidden="true" /> Download Puppyone</a>
    </header>
    {content}
  </main>;
}

function ResourceView({ projectId, path, userId, pathBytes }: ResourceLocation & { userId: string }) {
  const router = useWorkspaceRouter();
  const location = `${path}:${pathBytes ?? ''}`;
  const [pagination, setPagination] = useState<{ location: string; cursors: string[] }>({ location, cursors: [] });
  const pages = pagination.location === location ? pagination.cursors : [];
  const [copyResult, setCopyResult] = useState({ location, message: '' });
  const copyStatus = copyResult.location === location ? copyResult.message : '';
  const { data, error, isLoading, mutate } = useSWR(
    resourceKey(userId, projectId, path, pathBytes, pages.at(-1)),
    () => readWorkspaceResource(projectId, path, pages.at(-1), pathBytes),
    { shouldRetryOnError: false, revalidateOnFocus: false, keepPreviousData: false },
  );
  const directory = useCachedDirectory(data, userId, projectId, path);
  useEffect(() => {
    if (!data) return;
    const canonical = workspaceEntryPath(projectId, data.entry);
    if (window.location.pathname + window.location.search !== canonical) router.replace(canonical, { scroll: false });
  }, [data, projectId, router]);
  const copy = async () => {
    try {
      const locator = data ? workspaceEntryPath(projectId, data.entry) : window.location.pathname + window.location.search;
      await navigator.clipboard.writeText(window.location.origin + locator);
      setCopyResult({ location, message: 'Link copied. Recipients need workspace access.' });
    } catch { setCopyResult({ location, message: 'Could not copy. You can copy the address from your browser.' }); }
  };
  return <>
    <nav className={styles.breadcrumbs} aria-label="Workspace path">
      <Link href={workspaceResourcePath(projectId)} prefetch={false}><Folder size={15} aria-hidden="true" /> Workspace</Link>
      {path.split('/').filter(Boolean).map((name, index, parts) => <span key={index}>
        <ChevronRight size={13} aria-hidden="true" />
        {index === parts.length - 1 ? <span aria-current="page">{name}</span> :
          <Link href={workspaceResourcePath(projectId, parts.slice(0, index + 1).join('/'), true)} prefetch={false}>{name}</Link>}
      </span>)}
      <button className={styles.copy} onClick={() => void copy()} aria-label="Copy link"><Link2 size={15} aria-hidden="true" /><span>Copy link</span></button>
    </nav>
    {copyStatus && <p className={styles.notice} role="status">{copyStatus}</p>}
    <div className={styles.browser}>
      <DirectorySidebar data={data} directory={directory} projectId={projectId} isLoading={isLoading} pages={pages}
        paginate={cursors => setPagination({ location, cursors })} />
      <PreviewPane data={data} error={error} isLoading={isLoading} projectId={projectId} userId={userId}
        retry={() => { if (pages.length) setPagination({ location, cursors: [] }); else void mutate(); }} />
    </div>
    <footer className={styles.footer}><span>Preview your cloud files here.</span> Open Puppyone to work with the full workspace.</footer>
  </>;
}

/** Retain the already-authorized parent listing across Next page remounts, without a parent read. */
function useCachedDirectory(data: WorkspaceResource | undefined, userId: string, projectId: string, path: string) {
  const { cache } = useSWRConfig();
  const previous = useRef<WorkspaceResource | null>(null);
  if (data?.kind === 'directory') { previous.current = data; return data; }
  const parentPath = path.split('/').slice(0, -1).join('/');
  const cached = cache.get(unstable_serialize(resourceKey(userId, projectId, parentPath)))?.data as WorkspaceResource | undefined;
  const candidates = [previous.current, cached];
  if (data?.kind !== 'file') return null;
  return candidates.find(directory => directory?.kind === 'directory' && directory.entries.some(entry => sameEntry(entry, data.entry))) ?? null;
}

function PreviewPane({ data, error, isLoading, projectId, userId, retry }: {
  data?: WorkspaceResource; error?: { status: number }; isLoading: boolean; projectId: string; userId: string; retry: () => void;
}) {
  return <section className={styles.preview} aria-label="File preview">
    {isLoading && <p className={styles.loading} role="status">Loading preview…</p>}
    {error && <PreviewError status={error.status} retry={retry} />}
    {!error && data?.kind === 'file' && <FilePreview resource={data} />}
    {!error && data?.kind === 'directory' && <DirectoryPreview resource={data} projectId={projectId} userId={userId} />}
  </section>;
}

function DirectorySidebar({ data, directory, projectId, isLoading, pages, paginate }: {
  data?: WorkspaceResource; directory: WorkspaceResource | null; projectId: string; isLoading: boolean; pages: string[]; paginate: (cursors: string[]) => void;
}) {
  return <aside className={styles.sidebar} aria-label="Workspace files">
        <header className={styles.sidebarHeader}><Folder size={16} aria-hidden="true" /><h1>{directory?.entry.name || 'Workspace'}</h1><span>Files</span></header>
        <div className={styles.directoryScroll}>
          {directory ? <Directory resource={directory} projectId={projectId} selected={data?.kind === 'file' ? data.entry : defaultPreview(directory.entries)} /> :
            data?.kind === 'file' && <Directory resource={{ ...data, entries: [data.entry] }} projectId={projectId} selected={data.entry} />}
          {isLoading && !data && <p className={styles.loading} role="status">Loading files…</p>}
        </div>
        {data?.kind === 'directory' && (pages.length > 0 || data.next_cursor) && <nav className={styles.pagination} aria-label="Directory pages">
          <button disabled={!pages.length} onClick={() => paginate(pages.slice(0, -1))}>Previous</button>
          <span>{pages.length + 1}</span>
          <button disabled={!data.next_cursor} onClick={() => paginate([...pages, data.next_cursor!])}>Next</button>
        </nav>}
        <div className={styles.accessNote}><LockKeyhole size={13} aria-hidden="true" /><span>Workspace access required</span></div>
      </aside>;
}

function Directory({ resource, projectId, selected }: { resource: WorkspaceResource; projectId: string; selected?: ResourceEntry }) {
  const entries = visibleEntries(resource.entries);
  if (!entries.length) return <p className={styles.directoryEmpty}>This folder is empty.</p>;
  return <ul className={styles.entries}>{entries.map(entry => <li key={entry.path_bytes_b64 ?? entry.path}>
    <Link href={workspaceEntryPath(projectId, entry)} prefetch={false} aria-current={selected && sameEntry(entry, selected) ? 'page' : undefined}>
      {entry.type === 'folder' ? <Folder size={16} aria-hidden="true" /> : <File size={16} aria-hidden="true" />}
      <span>{entry.name}</span>{entry.type === 'folder' && <ChevronRight size={13} aria-hidden="true" />}
    </Link>
  </li>)}</ul>;
}

function DirectoryPreview({ resource, projectId, userId }: { resource: WorkspaceResource; projectId: string; userId: string }) {
  const first = defaultPreview(resource.entries);
  const { data, error, isLoading, mutate } = useSWR(first ? resourceKey(userId, projectId, first.path, first.path_bytes_b64 ?? undefined) : null,
    () => readWorkspaceResource(projectId, first!.path, undefined, first!.path_bytes_b64 ?? undefined),
    { shouldRetryOnError: false, revalidateOnFocus: false, keepPreviousData: false });
  if (isLoading) return <p className={styles.loading} role="status">Loading preview…</p>;
  if (error) return <PreviewError status={error.status} retry={() => void mutate()} />;
  if (data) return <FilePreview resource={data} />;
  return <div className={styles.empty}><File size={30} aria-hidden="true" /><h2>Select a file to preview</h2><p>Browse this workspace and read a document here.</p></div>;
}

function FilePreview({ resource }: { resource: WorkspaceResource }) {
  return <><header className={styles.previewHeader}><File size={16} aria-hidden="true" /><span>{resource.entry.name}</span><span className={styles.readOnly}>Read-only preview</span></header>
    <div className={styles.contentScroll}><ResourceContent resource={resource} /></div></>;
}
function sameEntry(a: ResourceEntry, b: ResourceEntry) { return (a.path_bytes_b64 ?? a.path) === (b.path_bytes_b64 ?? b.path); }
function defaultPreview(entries: ResourceEntry[]) { return visibleEntries(entries).find(entry => entry.type !== 'folder' && /\.(md|markdown|txt|json|csv)$/i.test(entry.name)); }
function visibleEntries(entries: ResourceEntry[]) { return entries.filter(entry => !['.DS_Store', 'Thumbs.db', '.git'].includes(entry.name)); }
function PreviewError({ status, retry }: { status: number; retry: () => void }) {
  const unavailable = status === 403 || status === 404;
  let message = 'Please try again.';
  if (unavailable) message = 'The path may have moved, or your account may not have workspace access.';
  else if (status === 409) message = 'The workspace changed. Reload this directory to continue.';
  return <section className={styles.empty} role="alert"><h1>{unavailable ? 'This resource is not available' : 'Could not load this preview'}</h1><p>{message}</p><button onClick={retry}>Retry</button></section>;
}
