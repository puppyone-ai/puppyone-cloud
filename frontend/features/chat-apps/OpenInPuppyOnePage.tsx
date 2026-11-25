'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams, useSearchParams } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { getProject } from '@/lib/projectsApi';
import { desktopProjectUrl, isMobileBrowser, webAgentUrl } from './desktop-link';
import { Button, Message, Screen } from './screen';
import { useLoginGate } from './useLoginGate';

const DESKTOP_WAIT_MS = 1500;

type State = 'checking' | 'noAccess' | 'failed' | 'opening';

/** Landing page for "Open in PuppyOne" links posted by the Agent in a chat. */
export function OpenInPuppyOnePage() {
  const t = useTranslations('chatApps.open');
  const params = useParams<{ projectId: string }>();
  const projectId = Array.isArray(params?.projectId) ? params.projectId[0] : params?.projectId ?? '';
  const sessionId = useSearchParams()?.get('session') || null;
  const { isAuthReady, userId } = useLoginGate();
  const [state, setState] = useState<State>('checking');
  const [attempt, setAttempt] = useState(0);

  const openInBrowser = useCallback(
    () => window.location.replace(webAgentUrl(projectId, sessionId)),
    [projectId, sessionId],
  );

  // Access check: the project read is the single source of truth for "may this user see it".
  useEffect(() => {
    if (!isAuthReady || !userId || !projectId) return;
    let active = true;
    setState('checking');
    getProject(projectId).then(
      () => { if (active) setState('opening'); },
      (error: { status?: number }) => {
        if (active) setState(error?.status === 403 || error?.status === 404 ? 'noAccess' : 'failed');
      },
    );
    return () => { active = false; };
  }, [isAuthReady, userId, projectId, attempt]);

  // Hand-off: phones go straight to the Web Agent; desktops try the app first.
  useEffect(() => {
    if (state !== 'opening') return;
    if (isMobileBrowser()) { openInBrowser(); return; }
    let timer: ReturnType<typeof setTimeout> | undefined;
    const cancel = () => { if (document.visibilityState === 'hidden') clearTimeout(timer); };
    document.addEventListener('visibilitychange', cancel);
    window.addEventListener('blur', () => clearTimeout(timer), { once: true });
    timer = setTimeout(() => {
      if (document.visibilityState === 'visible' && document.hasFocus()) openInBrowser();
    }, DESKTOP_WAIT_MS);
    window.location.assign(desktopProjectUrl(window.location.hostname, projectId, sessionId));
    return () => { clearTimeout(timer); document.removeEventListener('visibilitychange', cancel); };
  }, [state, projectId, sessionId, openInBrowser]);

  if (state === 'noAccess') return <Screen><Message strong>{t('noAccess')}</Message></Screen>;
  if (state === 'failed') {
    return <Screen><Message>{t('failed')}</Message><Button onClick={() => setAttempt(n => n + 1)}>{t('retry')}</Button></Screen>;
  }
  if (state === 'opening') {
    return (
      <Screen>
        <Message strong>{t('opening')}</Message>
        <Message>{t('openingHint')}</Message>
        <Button onClick={openInBrowser}>{t('openInBrowser')}</Button>
      </Screen>
    );
  }
  return <Screen><Message>{isAuthReady && !userId ? t('signIn') : t('checking')}</Message></Screen>;
}
