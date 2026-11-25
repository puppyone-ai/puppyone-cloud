'use client';

import { useTranslations } from 'next-intl';
import { useProjectActivity } from '@/lib/hooks/useActivity';
import { ChatAppActivityList } from './ChatAppActivityList';

/** Compact "Activity" section for the project home: chat-app management history, hidden when empty. */
export function ProjectChatAppActivity({ projectId }: Readonly<{ projectId: string }>) {
  const t = useTranslations('chatApps');
  const { items } = useProjectActivity(projectId, { kind: 'chat_app', activeOnly: false, limit: 5 });
  if (!items.some(item => item.kind === 'chat_app')) return null;
  return (
    <section aria-label={t('activityTitle')} style={{ marginTop: 24 }}>
      <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--po-text)', margin: '0 0 8px' }}>{t('activityTitle')}</h2>
      <ChatAppActivityList items={items} />
    </section>
  );
}
