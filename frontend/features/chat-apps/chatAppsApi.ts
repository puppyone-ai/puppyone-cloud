import { post } from '@/lib/apiClient';

export type ChatIdentityPreview = {
  platform: 'telegram' | 'slack' | 'wecom';
  name: string | null;
  chat: string | null;
};

/** Whose chat account a "Link account" code is for. Links nothing. */
export function previewChatIdentity(code: string): Promise<ChatIdentityPreview> {
  return post('/api/v1/chat-apps/identities/preview', { code });
}

/** Redeems the one-time code from a chat's "Link account" button. */
export function linkChatIdentity(code: string): Promise<unknown> {
  return post('/api/v1/chat-apps/identities', { code });
}
