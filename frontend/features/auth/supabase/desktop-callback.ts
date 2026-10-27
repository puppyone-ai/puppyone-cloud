import { relayAuthorizationCode } from './code-relay';

/** Stateless authorization-code relay. Only Desktop owns the PKCE verifier. */
export function desktopCallbackTarget(source: URL): string {
  return relayAuthorizationCode(
    source,
    'desktop',
    desktopCallbackUrl(source.searchParams.get('desktop_callback') ?? '')
  );
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
