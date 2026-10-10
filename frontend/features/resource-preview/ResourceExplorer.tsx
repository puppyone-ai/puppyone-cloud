'use client';

import { useState } from 'react';
import { WorkspaceLink } from '@/features/workspace/navigation';
import { workspaceEntryPath } from '@puppyone/cloud-core';
import { FileGlyphIcon } from '@/lib/fileIcons';
import { TreeDisclosureMarker } from '@/components/ui/TreeDisclosureMarker';
import { DirectoryLoadingState } from '@/components/loading/DirectoryLoadingState';
import { ReadErrorState } from '@/components/loading/ReadErrorState';
import { explorerRowStyle, explorerLabelStyle, explorerIconStyle, TreeIndentGuide } from '@/features/files/components/explorer/ExplorerRowChrome';
import { useWorkspaceActions } from '@/features/workspace/responsive';
import type { ResourceEntry } from './resourceApi';
import { useResource, entryIdentity, visibleEntries } from './resourceQueries';
import styles from './resource-preview.module.css';

type ExplorerProps = { userId: string; projectId: string; currentPath: string; selected?: ResourceEntry };

export function ResourceExplorer(props: ExplorerProps) {
  return <aside className={styles.explorer} aria-label="Workspace files">
    <div className={styles.treeScroll}><Directory {...props} path="" depth={0} /></div>
  </aside>;
}

function Directory({ path, bytes, depth, ...props }: ExplorerProps & { path: string; bytes?: string; depth: number }) {
  const [cursors, setCursors] = useState<string[]>([]);
  const { data, error, isLoading, mutate } = useResource(props.userId, props.projectId, path, bytes, cursors.at(-1));
  if (error) return <ReadErrorState label="Could not load files." onRetry={() => { if (cursors.length) setCursors([]); else void mutate(); }} />;
  if (isLoading) return <DirectoryLoadingState />;
  if (!data || data.kind !== 'directory') return null;
  const entries = visibleEntries(data.entries);
  return <>
    {entries.map(entry => <Entry key={entryIdentity(entry)} {...props} entry={entry} depth={depth} />)}
    {!entries.length && <p className={styles.treeEmpty}>Empty folder</p>}
    {(cursors.length > 0 || data.next_cursor) && <nav className={styles.pagination} aria-label="Directory pages">
      <button disabled={!cursors.length} onClick={() => setCursors(cursors.slice(0, -1))}>Previous</button>
      <span>{cursors.length + 1}</span>
      <button disabled={!data.next_cursor} onClick={() => setCursors([...cursors, data.next_cursor!])}>Next</button>
    </nav>}
  </>;
}

function Entry({ entry, depth, ...props }: ExplorerProps & { entry: ResourceEntry; depth: number }) {
  const folder = entry.type === 'folder';
  const [expanded, setExpanded] = useState<boolean>();
  const open = folder && (expanded ?? (props.currentPath === entry.path || props.currentPath.startsWith(`${entry.path}/`)));
  const selected = props.selected ? entryIdentity(props.selected) === entryIdentity(entry) : props.currentPath === entry.path;
  const { setFilesOpen } = useWorkspaceActions();
  return <>
    <div className={styles.treeRow} data-selected={selected} style={explorerRowStyle}>
      <TreeIndentGuide depth={depth} />
      <div style={explorerLabelStyle(depth)}>
        {folder ? <button className={styles.disclosure} style={explorerIconStyle} aria-label={`${open ? 'Collapse' : 'Expand'} ${entry.name}`} aria-expanded={open}
          onClick={() => setExpanded(!open)}><TreeDisclosureMarker expanded={open} /></button> :
          <span style={explorerIconStyle}><FileGlyphIcon name={entry.name} type={entry.type} size={18} /></span>}
        <WorkspaceLink className={styles.treeLink} href={workspaceEntryPath(props.projectId, entry)} prefetch={false}
          aria-current={selected ? 'page' : undefined} onClick={event => {
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
            if (folder) setExpanded(true);
            setFilesOpen(false);
          }}>{entry.name}</WorkspaceLink>
      </div>
    </div>
    {open && <Directory {...props} path={entry.path} bytes={entry.path_bytes_b64 ?? undefined} depth={depth + 1} />}
  </>;
}
