import { apiRawRequest } from '@/lib/apiClient';

// The chat-apps routes answer with the plain JSON body (no `{code, data}` envelope).
const send = <T>(path: string, body: unknown) =>
  apiRawRequest<T>(path, { method: 'POST', body: JSON.stringify(body) });

export type ChatIdentityPreview = {
  platform: 'telegram' | 'slack' | 'wecom';
  name: string | null;
  chat: string | null;
};

/** Whose chat account a "Link account" code is for. Links nothing. */
export function previewChatIdentity(code: string): Promise<ChatIdentityPreview> {
  return send('/api/v1/chat-apps/identities/preview', { code });
}

/** Redeems the one-time code from a chat's "Link account" button. */
export function linkChatIdentity(code: string): Promise<unknown> {
  return send('/api/v1/chat-apps/identities', { code });
}
