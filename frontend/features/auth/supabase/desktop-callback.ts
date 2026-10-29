import { relayAuthorizationCode } from './code-relay';

/** Stateless authorization-code relay. Only Desktop owns the PKCE verifier. */
export function desktopCallbackTarget(source: URL): string {
  return relayAuthorizationCode(
    source,
    'desktop',
    desktopCallbackUrl(restoreDesktopLoopbackHost(source.searchParams.get('desktop_callback') ?? ''))
  );
}

/**
 * Next.js rewrites the first `127.x.x.x`, `[::1]` or `localhost` anywhere in a
 * server request URL to `localhost` (next/dist/server/web/next-url.js). On a
 * loopback dev server the request host absorbs that rewrite; on every deployed
 * server it lands on this query value instead. Desktop only listens on
 * 127.0.0.1, so a server-side `localhost` callback is restored before the
 * strict check. Browser-side reads see the original value and stay strict.
 */
function restoreDesktopLoopbackHost(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return value;
  }
  if (url.protocol !== 'http:' || url.hostname !== 'localhost') return value;
  url.hostname = '127.0.0.1';
  return url.toString();
}

/** Only the exact per-attempt IP loopback endpoint can receive native auth. */
export function desktopCallbackUrl(value: string): URL {
  const target = new URL(value);
  if (target.protocol !== 'http:' || target.hostname !== '127.0.0.1'
    || !target.port || Number(target.port) < 1024 || target.pathname !== '/auth/callback'
    || target.username || target.password || target.search || target.hash)
    throw new Error('Invalid Desktop callback');
  return target;
}
