import { relayAuthorizationCode } from './code-relay';

const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

/**
 * Exact PuppyOne website origins allowed to receive a delegated sign-in.
 * Configured per environment; empty means website sign-in is disabled.
 */
export function allowedWebsiteOrigins(
  value: string = process.env.NEXT_PUBLIC_AUTH_WEBSITE_ORIGINS ?? ''
): string[] {
  const origins: string[] = [];
  for (const entry of value.split(',').map(item => item.trim()).filter(Boolean)) {
    let url: URL;
    try {
      url = new URL(entry);
    } catch {
      continue;
    }
    const loopback = LOOPBACK_HOSTS.has(url.hostname);
    if (
      (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback))
      || url.username || url.password || url.search || url.hash
      || (url.pathname !== '/' && url.pathname !== '')
    )
      continue;
    origins.push(url.origin);
  }
  return origins;
}

/** Only `<allowlisted website origin>/auth/callback`, without credentials or query. */
export function websiteCallbackUrl(value: string, allowed = allowedWebsiteOrigins()): URL {
  const target = new URL(value);
  if (
    !allowed.includes(target.origin)
    || target.pathname !== '/auth/callback'
    || target.username || target.password || target.search || target.hash
  )
    throw new Error('Invalid website callback');
  return target;
}

/** Stateless authorization-code relay. Only the website owns the PKCE verifier. */
export function websiteCallbackTarget(source: URL): string {
  return relayAuthorizationCode(
    source,
    'website',
    websiteCallbackUrl(source.searchParams.get('website_callback') ?? '')
  );
}
