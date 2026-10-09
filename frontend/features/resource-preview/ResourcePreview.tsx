'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useWorkspaceRouter } from '@/features/workspace/navigation';
import useSWR from 'swr';
import { File, Folder, Link2, Download, ChevronRight } from 'lucide-react';
import { workspaceResourcePath, workspaceByteResourcePath, workspaceEntryPath } from '@puppyone/cloud-core';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import { readWorkspaceResource, type WorkspaceResource } from './resourceApi';
import { ResourceContent } from './ResourceContent';
import styles from './resource-preview.module.css';

const DOWNLOAD_URL = 'https://www.puppyone.ai/en/download';

type ResourceLocation = { projectId: string; path: string; pathBytes?: string };

export function ResourcePreview({ projectId, path, pathBytes }: ResourceLocation) {
  const { isAuthReady, session, authError } = useAuth();
  const returnPath = pathBytes ? workspaceByteResourcePath(projectId, pathBytes) : workspaceResourcePath(projectId, path);
  let content;
  if (!isAuthReady) content = <p role="status">{authError || 'Loading workspace…'}</p>;
  else if (!session) content = <section className={styles.empty}>
    <h1>Sign in to view this workspace</h1>
    <p>Use an account that has access to this workspace.</p>
    <Link href={`/login?${new URLSearchParams({ redirect: returnPath })}`}>Sign in</Link>
  </section>;
  else content = <ResourceView key={`${session.user.id}:${projectId}:${path}:${pathBytes ?? ""}`} projectId={projectId} path={path} pathBytes={pathBytes} userId={session.user.id} />;
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <Link href="/home" className={styles.brand}>Puppyone</Link>
        <a className={styles.download} href={DOWNLOAD_URL}><Download size={16} /> Download Puppyone</a>
      </header>
      {content}
      <footer className={styles.footer}>Preview your cloud files here. Open Puppyone to work with the full workspace.</footer>
    </main>
  );
}

function ResourceView({ projectId, path, userId, pathBytes }: ResourceLocation & { userId: string }) {
  const router = useWorkspaceRouter();
  const [pages, setPages] = useState<string[]>([]);
  const [copyStatus, setCopyStatus] = useState('');
  const cursor = pages.at(-1);
  const { data, error, isLoading, mutate } = useSWR(
    ['workspace-resource', userId, projectId, path, pathBytes ?? '', cursor ?? ''],
    () => readWorkspaceResource(projectId, path, cursor, pathBytes),
    { shouldRetryOnError: false, revalidateOnFocus: false, keepPreviousData: false },
  );
  useEffect(() => {
    if (!data) return;
    const canonical = workspaceEntryPath(projectId, data.entry);
    if (window.location.pathname + window.location.search !== canonical) router.replace(canonical, { scroll: false });
  }, [data, projectId, path, router]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(window.location.origin + (data ? workspaceEntryPath(projectId, data.entry) : window.location.pathname + window.location.search));
      setCopyStatus('Link copied. Recipients need workspace access.');
    } catch { setCopyStatus('Could not copy. You can copy the address from your browser.'); }
  };
  return (
    <>
      <nav className={styles.breadcrumbs} aria-label="Workspace path">
        <Link href={workspaceResourcePath(projectId)} prefetch={false}>Workspace</Link>
        {path.split('/').filter(Boolean).map((name, index, parts) => (
          <span key={index}>
            <ChevronRight size={14} aria-hidden="true" />
            {index === parts.length - 1 ? <span aria-current="page">{name}</span> : (
              <Link href={workspaceResourcePath(projectId, parts.slice(0, index + 1).join('/'), true)} prefetch={false}>{name}</Link>
            )}
          </span>
        ))}
      </nav>
      {isLoading && <p role="status">Loading preview…</p>}
      {error && <PreviewError status={error.status} retry={() => { if (pages.length) setPages([]); else void mutate(); }} />}
      {!error && data && (
        <section className={styles.resource}>
          <div className={styles.title}>
            {data.kind === 'directory' ? <Folder size={24} /> : <File size={24} />}
            <h1>{data.entry.name || 'Workspace'}</h1>
            <button onClick={() => void copy()} aria-label="Copy link"><Link2 size={16} /> Copy link</button>
          </div>
          {copyStatus && <p className={styles.notice} role="status">{copyStatus}</p>}
          {data.kind === 'directory' ? <Directory resource={data} projectId={projectId} /> : <ResourceContent resource={data} />}
          {data.kind === 'directory' && (pages.length > 0 || data.next_cursor) && (
            <nav className={styles.pagination} aria-label="Directory pages">
              <button disabled={!pages.length} onClick={() => setPages(value => value.slice(0, -1))}>Previous</button>
              <span>Page {pages.length + 1}</span>
              <button disabled={!data.next_cursor} onClick={() => setPages(value => [...value, data.next_cursor!])}>Next</button>
            </nav>
          )}
        </section>
      )}
    </>
  );
}

function Directory({ resource, projectId }: { resource: WorkspaceResource; projectId: string }) {
  if (!resource.entries.length) return <p className={styles.empty}>This folder is empty.</p>;
  return <ul className={styles.entries}>{resource.entries.map(entry => (
    <li key={entry.path_bytes_b64 ?? entry.path}>
      <Link href={workspaceEntryPath(projectId, entry)} prefetch={false}>
        {entry.type === 'folder' ? <Folder size={18} /> : <File size={18} />}
        <span>{entry.name}</span><ChevronRight size={16} aria-hidden="true" />
      </Link>
    </li>
  ))}</ul>;
}

function PreviewError({ status, retry }: { status: number; retry: () => void }) {
  const unavailable = status === 403 || status === 404;
  let message = 'Please try again.';
  if (unavailable) message = 'The path may have moved, or your account may not have workspace access.';
  else if (status === 409) message = 'The workspace changed. Reload this directory to continue.';
  return <section className={styles.empty} role="alert">
    <h1>{unavailable ? 'This resource is not available' : 'Could not load this preview'}</h1>
    <p>{message}</p><button onClick={retry}>Retry</button>
  </section>;
}
