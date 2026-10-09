/** Human-facing locators. A URL conveys no membership or access credential. */
const PROJECT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isWorkspaceId(value: string): boolean {
  return PROJECT_ID.test(value);
}

export function isWorkspaceResourcePath(pathname: string): boolean {
  return isWorkspaceId(pathname.split('/')[1] ?? '');
}

/** Decode encoded route segments exactly once, before validating separators. */
export function decodeWorkspacePath(segments: readonly string[]): string {
  const decoded = segments.map(segment => decodeURIComponent(segment));
  if (decoded.some(segment => segment.includes('/'))) throw new Error('Invalid workspace path');
  return decoded.join('/');
}

export function workspaceResourcePath(projectId: string, path = '', directory = false): string {
  if (!isWorkspaceId(projectId)) throw new Error('Invalid workspace ID');
  const segments = path ? path.split('/') : [];
  if (segments.some(segment => !segment || segment === '.' || segment === '..' || /[\u0000-\u001f\u007f\\]/.test(segment))) {
    throw new Error('Invalid workspace path');
  }
  const encoded = segments.map(segment => encodeURIComponent(segment)).join('/');
  return `/${projectId.toLowerCase()}/${encoded}${directory && encoded ? '/' : ''}`;
}

export function workspaceResourceUrl(origin: string, projectId: string, path = '', directory = false): string {
  const base = new URL(origin);
  if (!['https:', 'http:'].includes(base.protocol) || base.username || base.password) {
    throw new Error('Invalid workspace website origin');
  }
  return `${base.origin}${workspaceResourcePath(projectId, path, directory)}`;
}

/** Lossless escape hatch for Git names a Unicode website path cannot represent. */
export function workspaceByteResourcePath(projectId: string, pathBytes: string): string {
  return `${workspaceResourcePath(projectId)}?${new URLSearchParams({ path_bytes_b64: pathBytes })}`;
}

export function workspaceEntryPath(projectId: string, entry: {
  path: string; type: string; path_bytes_b64: string | null;
}): string {
  try {
    if (entry.path_bytes_b64) {
      const bytes = Uint8Array.from(atob(entry.path_bytes_b64), char => char.charCodeAt(0));
      if (new TextDecoder('utf-8', { fatal: true }).decode(bytes) !== entry.path) throw new Error('Byte path');
    }
    return workspaceResourcePath(projectId, entry.path, entry.type === 'folder');
  } catch {
    if (!entry.path_bytes_b64) throw new Error('Resource path cannot be represented');
    return workspaceByteResourcePath(projectId, entry.path_bytes_b64);
  }
}
