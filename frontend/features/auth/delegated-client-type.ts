/**
 * Kept apart from delegated-client so the callback relays can name a client
 * without importing the module that imports them.
 */
export type DelegatedClient = 'desktop' | 'website';
