/**
 * Desktop hand-off for "Open in PuppyOne" links from chat apps.
 * The scheme is derived from the web origin the user is already on, so no
 * extra build-time variable is needed: local development, internal/staging
 * and production each map to the Desktop channel with the matching scheme.
 */
export type DesktopScheme = 'puppyone' | 'puppyone-internal' | 'puppyone-development';

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '0.0.0.0']);

export function desktopSchemeForHost(hostname: string): DesktopScheme {
  const host = hostname.toLowerCase();
  if (LOCAL_HOSTS.has(host) || host.endsWith('.localhost')) return 'puppyone-development';
  if (/(^|[.-])(qubits|staging)([.-]|$)/.test(host)) return 'puppyone-internal';
  return 'puppyone';
}

function withSession(base: string, sessionId?: string | null): string {
  return sessionId ? `${base}?session=${encodeURIComponent(sessionId)}` : base;
}

export function desktopProjectUrl(hostname: string, projectId: string, sessionId?: string | null): string {
  return withSession(
    `${desktopSchemeForHost(hostname)}://cloud/projects/${encodeURIComponent(projectId)}`,
    sessionId,
  );
}

/** The Web Agent lives in the project's side panel; `agent=open` asks the project to open it. */
export function webAgentUrl(projectId: string, sessionId?: string | null): string {
  const query = new URLSearchParams({ agent: 'open' });
  if (sessionId) query.set('session', sessionId);
  return `/projects/${encodeURIComponent(projectId)}/data?${query}`;
}

export function isMobileBrowser(): boolean {
  if (typeof navigator === 'undefined') return false;
  if (/Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent)) return true;
  return typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches;
}
