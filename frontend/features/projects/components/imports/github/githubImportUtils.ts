export function normalizeGithubRepositoryUrl(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;

  const sshMatch = trimmed.match(/^git@github\.com:([^/\s]+)\/(.+?)(?:\.git)?$/i);
  if (sshMatch) {
    const owner = sshMatch[1]?.trim();
    const repo = sshMatch[2]?.replace(/\.git$/i, '').trim();
    if (owner && repo) {
      return `https://github.com/${owner}/${repo}`;
    }
  }

  const candidate = /^(?:https?:\/\/)/i.test(trimmed)
    ? trimmed
    : /^(?:www\.)?github\.com\//i.test(trimmed)
      ? `https://${trimmed}`
      : trimmed;

  try {
    const parsed = new URL(candidate);
    const host = parsed.hostname.toLowerCase();
    if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null;
    if (host !== 'github.com' && host !== 'www.github.com') return null;
    const parts = parsed.pathname.split('/').filter(Boolean);
    if (parts.length < 2) return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

export function getGithubImportSourceLabel(sourceUrl: string): string {
  try {
    const url = new URL(sourceUrl);
    return `${url.hostname}${url.pathname}`.replace(/\/$/, '');
  } catch {
    return sourceUrl;
  }
}

export function getGithubImportPhaseLabel(phase: string): string {
  switch (phase) {
    case 'queued':
      return 'Queued';
    case 'validating':
      return 'Preparing import';
    case 'fetching':
      return 'Fetching repository';
    case 'writing':
      return 'Writing files';
    case 'completed':
      return 'Completed';
    case 'failed':
      return 'Failed';
    default:
      return phase;
  }
}

export function getGithubImportErrorMessage(error: unknown): string {
  const fallback = 'Import failed. Check the repository URL and try again.';
  const raw = error instanceof Error ? error.message : fallback;
  const detailText = raw.startsWith('Import failed:')
    ? raw.slice('Import failed:'.length).trim()
    : raw;

  try {
    const parsed = JSON.parse(detailText) as { detail?: unknown };
    if (typeof parsed.detail === 'string' && parsed.detail.trim()) {
      return parsed.detail;
    }
  } catch {
    // Keep the server message below.
  }

  return detailText || fallback;
}
