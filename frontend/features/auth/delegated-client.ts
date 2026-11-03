import type { DelegatedClient } from './delegated-client-type';
import { desktopCallbackUrl } from './supabase/desktop-callback';
import { websiteCallbackUrl } from './supabase/website-callback';

export type { DelegatedClient };

/**
 * Clients that reuse this login page but own their PKCE verifier and session.
 * Desktop receives the result on its loopback listener; the PuppyOne website
 * receives it on its own allowlisted origin. Neither reads or writes the Cloud
 * Web cookie session.
 */
export const DELEGATED_CLIENTS: readonly DelegatedClient[] = ['desktop', 'website'];

export function isDelegatedClient(value: string | null): value is DelegatedClient {
  return value === 'desktop' || value === 'website';
}

export function delegatedCallbackUrl(client: DelegatedClient, value: string): URL {
  return client === 'desktop' ? desktopCallbackUrl(value) : websiteCallbackUrl(value);
}

const ORIGIN_LABEL: Record<DelegatedClient, string> = {
  desktop: 'Desktop',
  website: 'the PuppyOne website',
};

export function restartSignInMessage(client: DelegatedClient): string {
  return `Sign-in request is invalid or expired. Start sign-in again from ${ORIGIN_LABEL[client]}.`;
}

export function expiredSignInMessage(client: DelegatedClient): string {
  return `Sign-in request expired. Start sign-in again from ${ORIGIN_LABEL[client]}.`;
}
