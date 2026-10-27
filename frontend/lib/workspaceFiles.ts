import { apiRawRequest } from '@/lib/apiClient';
import * as published from '@/lib/contentTreeApi';
import { resolveFormat } from '@/lib/fileFormats';

export interface LiveWorkspace {
  id: string; generation: string; project_id: string; state: string; target_ref: string;
}
interface NativeEntry { name: string; kind: string; size: number; modified_ns: string }
interface NativeFile extends NativeEntry { content_base64: string; revision: string }
export const workspaceEndpoint = (project: string) => `/api/v1/projects/${encodeURIComponent(project)}/workbench`;
export const workspaceStatus = (project: string, signal?: AbortSignal) =>
  apiRawRequest<{workspace: LiveWorkspace | null}>(workspaceEndpoint(project), {signal});

const decode = (data: string) => Uint8Array.from(atob(data), c => c.charCodeAt(0));
const encode = (data: Uint8Array) => {
  let text = '';
  for (let offset = 0; offset < data.length; offset += 8192) text += String.fromCharCode(...data.subarray(offset, offset + 8192));
  return btoa(text);
};
const nodeType = (entry: NativeEntry): published.NodeType => {
  if (entry.kind === 'directory') return 'folder';
  const format = resolveFormat({name: entry.name, mimeType: null});
  if (format.id === 'markdown') return 'markdown';
  if (format.id === 'json') return 'json';
  return 'file';
};
function treeEntry(entry: NativeEntry, path: string): published.TreeEntry {
  return {name: entry.name, path, type: nodeType(entry), size_bytes: entry.size,
    content_hash: `native:${entry.modified_ns}:${entry.size}`, mime_type: null, children_count: null};
}

/** One captured workspace generation. Never falls back to published content. */
export function createWorkspaceFiles(workspace: LiveWorkspace) {
  const revisions = new Map<string, string>();
  const endpoint = workspaceEndpoint(workspace.project_id);
  function query(project: string, path: string) {
    if (project !== workspace.project_id) throw new Error('Workspace Project changed.');
    if (workspace.state !== 'running') throw new Error('Resume this workspace to access its files.');
    return new URLSearchParams({generation: workspace.generation, path});
  }
  async function nativeRead(project: string, path: string, signal?: AbortSignal) {
    const file = await apiRawRequest<NativeFile>(`${endpoint}/file?${query(project, path)}`, {signal});
    return file;
  }
  const listDir: typeof published.listDir = async (project, path = '', options) => {
    const value = await apiRawRequest<{entries: NativeEntry[]}>(`${endpoint}/files?${query(project, path)}`, options);
    const nodes = value.entries.map(entry => published.entryToNodeInfo(treeEntry(entry, path ? `${path}/${entry.name}` : entry.name), project));
    return {nodes, total: nodes.length};
  };
  const readFile: typeof published.readFile = async (project, path, signal) => {
    const file = await nativeRead(project, path, signal);
    revisions.set(path, file.revision);
    const text = new TextDecoder('utf-8', {fatal: true}).decode(decode(file.content_base64));
    let content = null;
    if (nodeType(file) === 'json') { try { content = JSON.parse(text); } catch { /* Source editor still shows invalid JSON. */ } }
    return {path, type: nodeType(file), content, content_text: text, content_hash: file.revision};
  };
  const stat: typeof published.stat = async (project, path, signal) => {
    const parent = published.getParentPath(path) ?? '';
    if (!path) return {path, type: 'folder', name: '', content_hash: null, exists: true};
    const data = await listDir(project, parent, {signal});
    const found = data.nodes.find(node => node.path === path);
    return found ? {...found, exists: true} : {path, type: 'file', name: path.split('/').at(-1)!, content_hash: null, exists: false};
  };
  const fetchRawBlob: typeof published.fetchRawBlob = async (project, path, options = {}) => {
    if (options.pathBytes) throw new Error('Byte-path links must be opened from published history.');
    const file = await nativeRead(project, path, options.signal);
    return new Blob([decode(file.content_base64)]);
  };
  const writeFile: typeof published.writeFile = async (...args) => {
    const [project, path, content, , , expectedRevision] = args;
    const text = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
    const result = await apiRawRequest<{revision: string}>(`${endpoint}/file?${query(project, path)}`, {
      method: 'PUT', body: JSON.stringify({path, content_base64: encode(new TextEncoder().encode(text ?? 'null')),
        expected_revision: expectedRevision ?? revisions.get(path) ?? null}),
    });
    revisions.set(path, result.revision);
    // A native save has no Git commit. Keep this wire distinction explicit.
    return {path, commit_id: '', merged: false, conflicts: []};
  };
  async function operation(project: string, action: string, path: string, destination?: string) {
    return apiRawRequest(`${endpoint}/files?${query(project, path)}`, {
      method: 'POST', body: JSON.stringify({action, path, destination}),
    });
  }
  const mkdir: typeof published.mkdir = async (project, path) => { await operation(project, 'mkdir', path); return {path}; };
  const moveFile: typeof published.moveFile = async (project, path, destination) => {
    await operation(project, 'move', path, destination); return {old_path: path, new_path: destination};
  };
  const removeFile: typeof published.removeFile = async (project, path) => { await operation(project, 'remove', path); return {path}; };
  const bulkRemoveFiles: typeof published.bulkRemoveFiles = async (project, paths) => {
    for (const path of paths) await removeFile(project, path);
    return {paths, commit_id: ''};
  };
  const downloadNode: typeof published.downloadNode = async (project, path) => {
    const blob = await fetchRawBlob(project, path);
    const url = URL.createObjectURL(blob);
    try { const a = document.createElement('a'); a.href = url; a.download = path.split('/').at(-1) || 'download'; a.click(); }
    finally { setTimeout(() => URL.revokeObjectURL(url), 1000); }
  };
  return {listDir, readFile, stat, fetchRawBlob, writeFile, mkdir, moveFile, removeFile, bulkRemoveFiles, downloadNode};
}

export type FileClient = ReturnType<typeof createWorkspaceFiles>;
export const publishedFiles: FileClient = published;

/** Capture the editor's displayed base, independently of preview/cache reads. */
export async function nativeTextRevision(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
