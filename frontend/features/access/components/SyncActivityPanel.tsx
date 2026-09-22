'use client';

/**
 * Sync activity + stats — scope-level observability for the managed sync
 * (PUP-sync-trigger M6). Surfaces the backend `/scope-sync/activity` (publish/
 * projection event log) + `/scope-sync/stats` (aggregate volume) so a user can
 * see what the sidecar published, when, by which end, and which paths moved.
 *
 * Renders nothing until there's at least one event (a scope with no sync history
 * shows no panel rather than an empty shell).
 */

import useSWR from 'swr';
import type { RepositoryView } from '@/lib/repoApi';
import {
  getSyncActivity,
  getSyncStats,
  type SyncActivityEvent,
} from '@/lib/scopeSyncApi';
import { T } from '@/features/access/lib/tokens';
import { SectionLabel } from '@/features/access/components/ui-blocks';

function relTime(epochSecs: number): string {
  const secs = Math.floor(Date.now() / 1000 - epochSecs);
  if (secs < 0) return 'just now';
  if (secs < 60) return 'just now';
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function shortHead(head: string | null): string {
  return head ? head.slice(0, 8) : '—';
}

function originLabel(origin: string | null): string {
  if (!origin) return 'unknown';
  // backend tags scope-keyed publishes as "scope:<id>"; show a compact form
  if (origin.startsWith('scope:')) return `scope ${origin.slice(6, 14)}`;
  return origin.length > 14 ? `${origin.slice(0, 14)}…` : origin;
}

function EventRow({ ev }: { readonly ev: SyncActivityEvent }) {
  const paths = ev.affected_paths ?? [];
  const pathSummary =
    paths.length === 0
      ? '(no paths)'
      : paths.length <= 2
        ? paths.join(', ')
        : `${paths.slice(0, 2).join(', ')} +${paths.length - 2} more`;
  return (
    <li
      style={{
        display: 'flex',
        alignItems: 'baseline',
        gap: 8,
        padding: '6px 0',
        borderBottom: `1px solid ${T.cardBorder}`,
        fontFamily: T.fontSans,
        fontSize: 12,
      }}
    >
      <span
        style={{
          flexShrink: 0,
          fontSize: 10,
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: 0.3,
          color: ev.source === 'publish' ? 'var(--po-success)' : T.text3,
        }}
      >
        {ev.source}
      </span>
      <span style={{ flex: 1, minWidth: 0, color: T.text2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={paths.join('\n')}>
        {pathSummary}
      </span>
      <span style={{ flexShrink: 0, color: T.text4, fontFamily: T.fontMono, fontSize: 11 }}>
        {shortHead(ev.head_version)}
      </span>
      <span style={{ flexShrink: 0, color: T.text3 }}>{originLabel(ev.origin_user)}</span>
      <span style={{ flexShrink: 0, color: T.text4 }}>{relTime(ev.created_at)}</span>
    </li>
  );
}

export function SyncActivityPanel({
  scope,
  projectId,
}: {
  readonly scope: RepositoryView;
  readonly projectId: string;
}) {
  const key = projectId && scope.id ? [projectId, scope.id] : null;
  const { data: activity } = useSWR(
    key ? ['scope-sync-activity', ...key] : null,
    () => getSyncActivity(projectId, scope.id, 15),
    { refreshInterval: 20000, revalidateOnFocus: false },
  );
  const { data: stats } = useSWR(
    key ? ['scope-sync-stats', ...key] : null,
    () => getSyncStats(projectId, scope.id),
    { refreshInterval: 30000, revalidateOnFocus: false },
  );

  const events = activity?.recent ?? [];
  if (events.length === 0) return null; // no sync history → no panel

  const sources = stats?.by_source ?? {};
  const sourceSummary = Object.entries(sources)
    .map(([s, n]) => `${n} ${s}`)
    .join(' · ');

  return (
    <div style={{ marginTop: 26 }}>
      <SectionLabel
        right={
          stats ? (
            <span style={{ fontSize: 12, color: T.text4, fontFamily: T.fontSans, fontWeight: 500 }}>
              {stats.distinct_origins} {stats.distinct_origins === 1 ? 'origin' : 'origins'} · {stats.distinct_paths} paths
            </span>
          ) : null
        }
      >
        Sync activity
      </SectionLabel>

      <div
        style={{
          borderRadius: 8,
          border: `1px solid ${T.cardBorder}`,
          background: T.cardBg,
          padding: 14,
          fontFamily: T.fontSans,
        }}
      >
        {sourceSummary ? (
          <div style={{ fontSize: 11, color: T.text3, marginBottom: 8 }}>
            {sourceSummary} in the last {stats?.window ?? 200} events
            {activity?.latest_head ? (
              <>
                {' · head '}
                <code style={{ fontFamily: T.fontMono }}>{shortHead(activity.latest_head)}</code>
              </>
            ) : null}
          </div>
        ) : null}
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {events.map((ev) => (
            <EventRow key={ev.id} ev={ev} />
          ))}
        </ul>
      </div>
    </div>
  );
}
