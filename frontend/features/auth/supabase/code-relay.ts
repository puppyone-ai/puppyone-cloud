import type { DelegatedClient } from '../delegated-client';

/**
 * Stateless authorization-code relay for a delegated client (Desktop or the
 * PuppyOne website). The delegated client alone owns the PKCE verifier, so
 * this page never exchanges the code and never writes a Web cookie. Only the
 * code (or a bounded cancellation) and the client's own state are forwarded.
 */
export function relayAuthorizationCode(source: URL, client: DelegatedClient, target: URL): string {
  const stateKey = `${client}_state`;
  for (const key of [stateKey, `${client}_callback`, 'code', 'error'])
    if (source.searchParams.getAll(key).length > 1) throw new Error('Ambiguous callback');
  const state = source.searchParams.get(stateKey) ?? '';
  if (!/^[A-Za-z0-9_-]{43,128}$/.test(state)) throw new Error(`Invalid ${client} callback`);
  target.searchParams.set('state', state);
  const code = source.searchParams.get('code');
  if (source.searchParams.has('error')) target.searchParams.set('error', 'access_denied');
  else if (code && /^[A-Za-z0-9_.~-]{1,2048}$/.test(code)) target.searchParams.set('code', code);
  else throw new Error('Missing authorization code');
  return target.toString();
}
