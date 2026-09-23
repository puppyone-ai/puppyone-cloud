import type { Session } from '@supabase/supabase-js';
import { backendAuthError } from './auth-errors';

const API = '/api/backend/api/v1/auth/desktop';

export async function bindDesktopBrowser(
  state: string,
  proof: string,
  fetcher: typeof fetch = fetch
) {
  const response = await fetcher(`${API}/bind`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ state, browser_proof: proof }),
    cache: 'no-store',
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok)
    throw backendAuthError(
      await response.json().catch(() => null),
      'Unable to prepare Desktop sign-in. Try again.'
    );
}

export async function completeDesktopHandoff(
  state: string,
  proof: string,
  session: Session,
  fetcher: typeof fetch = fetch
): Promise<string> {
  const response = await fetcher(`${API}/complete`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${session.access_token}`,
    },
    cache: 'no-store',
    signal: AbortSignal.timeout(25_000),
    body: JSON.stringify({
      state,
      browser_proof: proof,
      access_token: session.access_token,
      refresh_token: session.refresh_token,
    }),
  });
  const json = await response.json().catch(() => null);
  if (!response.ok)
    throw backendAuthError(json, 'Unable to complete Desktop sign-in. Start sign-in again.');
  const url = new URL(json?.data?.redirect_url);
  const local =
    url.protocol === 'http:' &&
    ['127.0.0.1', '[::1]'].includes(url.hostname) &&
    !!url.port &&
    url.pathname === '/auth/callback';
  const legacy =
    url.protocol === 'puppyone:' && url.hostname === 'auth' && url.pathname === '/callback';
  if (
    (!local && !legacy) ||
    url.username ||
    url.password ||
    url.hash ||
    url.searchParams.get('state') !== state ||
    !url.searchParams.get('code')
  )
    throw new Error('Desktop sign-in returned an invalid callback.');
  return url.toString();
}
