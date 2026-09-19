'use client';


export const HISTORY_DIFF_HEADER_BG = 'color-mix(in srgb, var(--po-canvas) 84%, var(--po-text) 4%)';

export const HISTORY_ROW_ITEM_HEIGHT = 30;

export const HISTORY_ROW_MARGIN_Y = 1;

export const HISTORY_ROW_HEIGHT = HISTORY_ROW_ITEM_HEIGHT + HISTORY_ROW_MARGIN_Y * 2;

export const HISTORY_GRAPH_WIDTH = 20;

export const HISTORY_LINE_X = HISTORY_GRAPH_WIDTH / 2;

export function fileExtClass(path: string): 'json' | 'markdown' | 'plain' {
  if (path.endsWith('.json')) return 'json';
  if (path.endsWith('.md') || path.endsWith('.markdown')) return 'markdown';
  return 'plain';
}

export function formatTime(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString(undefined, {
    month: 'short', day: 'numeric',
    year: d.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
  });
}

export function formatFullTime(iso: string | null): string {
  if (!iso) return '';
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

export function formatTimeShort(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function parseOperator(who: string): { type: string; id: string } {
  if (who.includes(':')) {
    const [type, ...rest] = who.split(':');
    return { type, id: rest.join(':') };
  }
  return { type: who || 'system', id: '' };
}

export function formatOperatorLabel(type: string): string {
  if (type === 'user') return 'User';
  if (type === 'agent') return 'Agent';
  if (type === 'sync') return 'Sync';
  return 'System';
}

export function formatScopeLabel(scopePath: string): string {
  const normalized = normalizeScopePath(scopePath);
  if (!normalized || normalized === '/') return 'Project root';
  return normalized;
}

export function normalizeScopePath(scopePath: string): string {
  return (scopePath || '').trim().replace(/^\/+|\/+$/g, '').replace(/\/+/g, '/');
}

export function scopeIncludesCommit(scopePath: string, commitScopePath: string): boolean {
  const scope = normalizeScopePath(scopePath);
  const commitScope = normalizeScopePath(commitScopePath);
  if (!scope) return true;
  return commitScope === scope || commitScope.startsWith(`${scope}/`);
}

export function scopeLineage(scopePath: string): string[] {
  const scope = normalizeScopePath(scopePath);
  if (!scope) return [''];
  const parts = scope.split('/').filter(Boolean);
  const lineage = [''];
  for (let index = 0; index < parts.length; index += 1) {
    lineage.push(parts.slice(0, index + 1).join('/'));
  }
  return lineage;
}

export function getTrackInfo(who: string) {
  const { type } = parseOperator(who);
  switch (type) {
    case 'user': return { color: 'var(--po-accent)' };
    case 'agent': return { color: 'var(--po-file-accent-audio)' };
    case 'sync': return { color: 'var(--po-success)' };
    default: return { color: 'var(--po-text-muted)' };
  }
}
