import { post } from '@/lib/apiClient';

/** Redeems the one-time code from a chat's "Link account" button. */
export function linkChatIdentity(code: string): Promise<unknown> {
  return post('/api/v1/chat-apps/identities', { code });
}
