'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { linkChatIdentity, previewChatIdentity, type ChatIdentityPreview } from './chatAppsApi';
import { Button, Message, Screen } from './screen';
import { useLoginGate } from './useLoginGate';

type State = 'checking' | 'confirm' | 'linking' | 'linked' | 'expired' | 'failed';

const APP_NAMES = { telegram: 'Telegram', slack: 'Slack', wecom: 'WeCom' } as const;
const gone = (error: { status?: number }) => error?.status === 409 || error?.status === 410;

/**
 * Redeems the one-time code from a chat's "Link account" button. The button may
 * be clicked by anyone who can see the chat, so the page first says whose chat
 * account this is and links only when the signed-in person confirms.
 */
export function AccountLinkPage() {
  const t = useTranslations('chatApps.link');
  const code = useSearchParams()?.get('code') || '';
  const { isAuthReady, userId } = useLoginGate();
  const [state, setState] = useState<State>('checking');
  const [who, setWho] = useState<ChatIdentityPreview | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!isAuthReady || !userId) return;
    if (!code) { setState('expired'); return; }
    let active = true;
    setState('checking');
    previewChatIdentity(code).then(
      found => { if (active) { setWho(found); setState('confirm'); } },
      error => { if (active) setState(gone(error) ? 'expired' : 'failed'); },
    );
    return () => { active = false; };
  }, [isAuthReady, userId, code, attempt]);

  const link = () => {
    setState('linking');
    linkChatIdentity(code).then(
      () => setState('linked'),
      error => setState(gone(error) ? 'expired' : 'failed'),
    );
  };

  if (!userId) return <Screen><Message>{isAuthReady ? t('signIn') : t('checking')}</Message></Screen>;
  if (state === 'linked') return <Screen><Message strong>{t('linked')}</Message></Screen>;
  if (state === 'expired') return <Screen><Message strong>{code ? t('expired') : t('missingCode')}</Message></Screen>;
  if (state === 'failed') {
    return <Screen><Message>{t('failed')}</Message><Button onClick={() => setAttempt(n => n + 1)}>{t('retry')}</Button></Screen>;
  }
  if (state === 'confirm' && who) {
    const app = APP_NAMES[who.platform];
    return (
      <Screen>
        <Message strong>{t('confirmTitle', { name: who.name || t('someone'), app })}</Message>
        {who.chat && <Message>{t('confirmChat', { chat: who.chat })}</Message>}
        <Message>{t('confirmHint', { app })}</Message>
        <Button primary onClick={link}>{t('confirm')}</Button>
      </Screen>
    );
  }
  return <Screen><Message>{state === 'linking' ? t('linking') : t('checking')}</Message></Screen>;
}
