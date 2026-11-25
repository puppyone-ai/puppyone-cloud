'use client';

import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { linkChatIdentity } from './chatAppsApi';
import { Button, Message, Screen } from './screen';
import { useLoginGate } from './useLoginGate';

type State = 'linking' | 'linked' | 'expired' | 'failed';

/** Redeems the one-time code from a chat's "Link account" button for the signed-in user. */
export function AccountLinkPage() {
  const t = useTranslations('chatApps.link');
  const code = useSearchParams()?.get('code') || '';
  const { isAuthReady, userId } = useLoginGate();
  const [state, setState] = useState<State>('linking');
  const [attempt, setAttempt] = useState(0);
  const started = useRef<string>('');

  useEffect(() => {
    if (!isAuthReady || !userId) return;
    if (!code) { setState('expired'); return; }
    // One redemption per user, code and attempt, including Strict Mode's effect replay.
    const key = `${userId}:${code}:${attempt}`;
    if (started.current === key) return;
    started.current = key;
    setState('linking');
    linkChatIdentity(code).then(
      () => setState('linked'),
      (error: { status?: number }) => setState(error?.status === 409 || error?.status === 410 ? 'expired' : 'failed'),
    );
  }, [isAuthReady, userId, code, attempt]);

  if (!userId) return <Screen><Message>{isAuthReady ? t('signIn') : t('linking')}</Message></Screen>;
  if (state === 'linked') return <Screen><Message strong>{t('linked')}</Message></Screen>;
  if (state === 'expired') return <Screen><Message strong>{code ? t('expired') : t('missingCode')}</Message></Screen>;
  if (state === 'failed') {
    return <Screen><Message>{t('failed')}</Message><Button onClick={() => setAttempt(n => n + 1)}>{t('retry')}</Button></Screen>;
  }
  return <Screen><Message>{t('linking')}</Message></Screen>;
}
