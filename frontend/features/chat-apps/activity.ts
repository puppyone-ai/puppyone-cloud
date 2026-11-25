import type { ActivityItem } from '@/lib/activityApi';

type Translate = (key: string, values?: Record<string, string>) => string;

const ACTIONS = new Set(['added', 'allowed', 'denied', 'updated', 'taken_over', 'removed', 'removed_there']);
const PLATFORMS = new Set(['telegram', 'slack', 'wecom']);

/** One-line history text for a `chat_app` activity row; `t` is scoped to `chatApps.activity`. */
export function chatAppActivityText(item: ActivityItem, t: Translate): string {
  const platform = PLATFORMS.has(item.phase ?? '') ? t(item.phase!) : t('chat');
  const values = { name: item.label || t('chat'), platform };
  return t(ACTIONS.has(item.status ?? '') ? item.status! : 'unknown', values);
}
