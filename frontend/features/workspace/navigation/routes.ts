export type WorkspaceLocation = {
  projectId: string;
  view: 'files' | 'git' | 'other';
  path: string[];
  typeHint: string;
  href: string;
};

function decode(segment: string) {
  try { return decodeURIComponent(segment); } catch { return segment; }
}

export function filesHref(projectId: string, path: readonly string[] = [], typeHint?: string) {
  const tail = path.map(encodeURIComponent).join('/');
  const base = `/projects/${encodeURIComponent(projectId)}/data${tail ? `/${tail}` : ''}`;
  return typeHint ? `${base}?type=${encodeURIComponent(typeHint)}` : base;
}

export function gitHref(projectId: string) {
  return `/projects/${encodeURIComponent(projectId)}/changes`;
}

export function parseWorkspaceLocation(pathname: string, search = ''): WorkspaceLocation | null {
  const parts = pathname.split('/').filter(Boolean);
  if (parts[0] !== 'projects' || !parts[1]) return null;
  const params = new URLSearchParams(search);
  return {
    projectId: decode(parts[1]),
    view: parts[2] === 'data' ? 'files' : parts[2] === 'changes' || parts[2] === 'history' ? 'git' : 'other',
    path: parts[2] === 'data' ? parts.slice(3).map(decode) : [],
    typeHint: params.get('type') ?? '',
    href: pathname + (params.size ? `?${params}` : ''),
  };
}

/** Remembered locations are bookmarks, never another source of current state. */
export function returnToFiles(projectId: string, remembered: string | null) {
  if (remembered) {
    const [pathname, search] = remembered.split('?');
    const location = parseWorkspaceLocation(pathname, search);
    if (location?.view === 'files' && location.projectId === projectId) return remembered;
  }
  return filesHref(projectId);
}
