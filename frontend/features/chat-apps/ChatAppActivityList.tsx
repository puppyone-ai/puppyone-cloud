'use client';

import { useTranslations } from 'next-intl';
import type { ActivityItem } from '@/lib/activityApi';
import { chatAppActivityText } from './activity';

/** Renders chat-app management history rows (kind `chat_app`) in the activity history style. */
export function ChatAppActivityList({ items }: Readonly<{ items: ActivityItem[] }>) {
  const t = useTranslations('chatApps.activity');
  const rows = items.filter(item => item.kind === 'chat_app');
  if (rows.length === 0) return null;
  return (
    <ul aria-label="Chat apps activity" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {rows.map(item => (
        <li key={item.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '6px 0', fontSize: 13, color: 'var(--po-text)' }}>
          <span>{chatAppActivityText(item, t as never)}</span>
          {item.created_at && (
            <time dateTime={item.created_at} style={{ color: 'var(--po-text-muted)', fontSize: 12 }}>
              {new Date(item.created_at).toLocaleString()}
            </time>
          )}
        </li>
      ))}
    </ul>
  );
}
