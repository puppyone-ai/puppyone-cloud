/** Stateless authorization-code relay. Only Desktop owns the PKCE verifier. */
export function desktopCallbackTarget(source: URL): string {
  for (const key of ['desktop_state', 'desktop_callback', 'code', 'error'])
    if (source.searchParams.getAll(key).length > 1) throw new Error('Ambiguous callback');
  const state = source.searchParams.get('desktop_state') ?? '';
  const target = desktopCallbackUrl(source.searchParams.get('desktop_callback') ?? '');
  if (!/^[A-Za-z0-9_-]{43,128}$/.test(state)) throw new Error('Invalid Desktop callback');
  target.searchParams.set('state', state);
  const code = source.searchParams.get('code');
  if (source.searchParams.has('error')) target.searchParams.set('error', 'access_denied');
  else if (code && /^[A-Za-z0-9_.~-]{1,2048}$/.test(code)) target.searchParams.set('code', code);
  else throw new Error('Missing authorization code');
  return target.toString();
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
