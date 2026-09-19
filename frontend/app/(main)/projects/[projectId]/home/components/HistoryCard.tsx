'use client';

import { useId } from 'react';
import type { useWorkspaceRouter as useRouter } from '@/features/workspace/navigation';
import { CountBadge } from '@/components/ui/CountBadge';
import { T } from '../lib/tokens';
import { formatRelative } from '../lib/format';
import type { VersionCommitInfo } from '@/lib/contentTreeApi';

// HistoryCard — Right-rail card showing the project's commit history
// in the layout the OLD GitHub-style page used: a vertical timeline of
// recent commits stacked on TOP of a full-width sparkline whose X axis
// reads as a real chart with dated tick labels.
//
// Layout:
//   ┌─ HISTORY  N                       View all → ┐
//   │ • commit message                              │
//   │ │   relative-time                             │
//   │ │                                             │
//   │ ○ commit message                              │
//   │ │   relative-time                             │
//   │ │                                             │
//   │ ○ … (up to 5)                                 │
//   │                                                │
//   │ ┌─────────────────────────────────────────┐  │
//   │ │ ░░░░ full-width 30d sparkline ░░░░░░░░  │  │
//   │ └─────────────────────────────────────────┘  │
//   │  03/24    03/31    04/07    04/14    04/21    │
//   └────────────────────────────────────────────────┘
//
// Why the timeline + spanning sparkline (instead of the earlier
// "list + tiny right-aligned sparkline" we shipped briefly):
//   ─ The vertical timeline line + dot column makes "this is a
//     sequence" unmistakable in one glance, and gives the most-recent
//     commit a visual anchor (cyan dot) that says "live, this just
//     happened".
//   ─ A full-width sparkline with dated ticks reads as a real CHART
//     ("here's the cadence over the last 30 days") rather than a
//     mood indicator.  X-axis labels (5 evenly-spaced MM/DD ticks)
//     turn the spark into a proper time-series — makes a "burst of
//     activity 2 weeks ago" actually findable.
//   ─ Visual hierarchy mirrors GitHub's repo-home Activity card,
//     which users have an established mental model for.

// catmull-rom → cubic bezier, alpha=0.  Endpoints duplicate so the
// curve doesn't snap at the boundary.  Same smoothing as
// HeaderSparkline so any chart on Home reads as the same component.
function smoothLinePath(pts: { x: number; y: number }[]): string {
  if (pts.length === 0) return '';
  if (pts.length === 1) {
    return `M${pts[0].x.toFixed(2)},${pts[0].y.toFixed(2)}`;
  }
  const segs: string[] = [`M${pts[0].x.toFixed(2)},${pts[0].y.toFixed(2)}`];
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] || pts[i + 1];
    const cp1x = p1.x + (p2.x - p0.x) / 6;
    const cp1y = p1.y + (p2.y - p0.y) / 6;
    const cp2x = p2.x - (p3.x - p1.x) / 6;
    const cp2y = p2.y - (p3.y - p1.y) / 6;
    segs.push(
      `C${cp1x.toFixed(2)},${cp1y.toFixed(2)} ${cp2x.toFixed(2)},${cp2y.toFixed(2)} ${p2.x.toFixed(2)},${p2.y.toFixed(2)}`,
    );
  }
  return segs.join(' ');
}

// Wide sparkline with X-axis tick labels.
// Stretches to the full width of its container via viewBox +
// `preserveAspectRatio="none"`. `vector-effect: non-scaling-stroke`
// keeps the line/dot the same visual weight regardless of the card's CSS box.
function WideSparkline({
  buckets,
  hasHistory,
  height = 56,
}: {
  buckets: { date: string; count: number }[];
  hasHistory: boolean;
  height?: number;
}) {
  const id = useId();
  const W = 200;
  const H = height;
  const max = Math.max(...buckets.map(b => b.count), 1);

  // 4px top + bottom padding inside the SVG so the line never grazes
  // the bounds and the area gradient gets a comfortable resting
  // baseline a couple px above the bottom edge.
  const pts = buckets.map((b, i) => ({
    x: buckets.length > 1 ? (i / (buckets.length - 1)) * W : W / 2,
    y: H - (b.count / max) * (H - 8) - 4,
  }));
  const linePath = smoothLinePath(pts);
  const baselineY = H - 0.5;
  const areaPath = pts.length
    ? `${linePath} L${pts[pts.length - 1].x.toFixed(2)},${baselineY.toFixed(2)} L${pts[0].x.toFixed(2)},${baselineY.toFixed(2)} Z`
    : '';
  const lastPt = pts[pts.length - 1];

  // Tick labels: 5 evenly spaced from the buckets array, formatted
  // MM/DD.  We pick by index (0, 25%, 50%, 75%, 100%) rather than
  // by calendar date so the spacing is uniform regardless of how the
  // bucket array was constructed.
  const labels =
    buckets.length >= 2
      ? [0, 0.25, 0.5, 0.75, 1].map(t => {
          const idx = Math.round(t * (buckets.length - 1));
          const d = buckets[idx]?.date ?? '';
          if (!d) return '';
          const [, m, dd] = d.split('-');
          return m && dd ? `${m}/${dd}` : '';
        })
      : [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <svg
        width="100%"
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ display: 'block', overflow: 'visible' }}
        aria-hidden="true"
      >
        <defs>
          {/* Same cyan gradient we use across all Home charts. */}
          <linearGradient id={`hcg-${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={T.live} stopOpacity={0.22} />
            <stop offset="100%" stopColor={T.live} stopOpacity={0} />
          </linearGradient>
        </defs>

        {hasHistory ? (
          <>
            <path d={areaPath} fill={`url(#hcg-${id})`} />
            <path
              d={linePath}
              fill="none"
              stroke={T.live}
              strokeWidth="1.5"
              strokeLinejoin="round"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
            {lastPt && (
              <circle
                cx={lastPt.x}
                cy={lastPt.y}
                r={2.5}
                fill={T.live}
                vectorEffect="non-scaling-stroke"
              />
            )}
          </>
        ) : (
          // Empty state: a single faint cyan baseline (no dashes).
          // Continuous low-opacity line is the convention used by
          // Vercel / Linear / Stripe sparklines and reads as "a
          // chart resting at zero" rather than no chart.
          <line
            x1={2}
            y1={H - 4}
            x2={W - 2}
            y2={H - 4}
            stroke={T.live}
            strokeWidth="1"
            strokeOpacity={0.28}
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>

      {/* X-axis labels — flex row spanning the same width as the SVG
          above.  `space-between` pins the first label to the left
          edge and the last label to the right edge, exactly under
          the sparkline's first and last data points.
          Theme sans, NOT mono — these are dates not code, and mono
          made them visually pop as a "data dump" instead of a
          quiet axis label. */}
      {labels.length > 0 && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: 10,
            color: T.text3,
            fontVariantNumeric: 'tabular-nums',
            fontFamily: T.fontSans,
          }}
        >
          {labels.map((l, i) => (
            <span key={i}>{l}</span>
          ))}
        </div>
      )}
    </div>
  );
}

export function HistoryCard({
  projectId,
  router,
  commits,
  buckets,
}: {
  projectId: string;
  router: ReturnType<typeof useRouter>;
  commits: VersionCommitInfo[];
  // 30-day daily commit counts (oldest → newest), already shaped by the
  // page so we don't recompute per render.
  buckets: { date: string; count: number }[];
}) {
  // commits arrive oldest-first from `getProjectHistory`; flip + slice
  // for newest-first display.  Hard cap at 5 — anything more crowds
  // the card and pushes the sparkline off the bottom of the right rail.
  const recent = commits.slice(-5).reverse();
  const total = commits.length;
  const hasHistory = total > 0;

  return (
    <div
      style={{
        // Section card surface: dark interior + visibly thick (2px)
        // border = the recessed framed panel from the OLD GitHub-
        // style page.  See `tokens.ts` → `sectionBg` /
        // `sectionBorder` / `sectionRadius`.
        background: T.sectionBg,
        border: `2px solid ${T.sectionBorder}`,
        borderRadius: T.sectionRadius,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Header — Title-Case label + count chip on the left, View
          all → on the right.  The label is normal-case 13px (not
          uppercase letter-spaced) to match the GitHub-style page
          where headers read as sentence fragments rather than tech
          dashboard tags. */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '10px 14px',
          background: T.sectionHeaderBg,
          borderBottom: `1px solid ${T.sectionDivider}`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span
            style={{
              fontSize: 13,
              fontWeight: 500,
              color: T.text2,
            }}
          >
            History
          </span>
          <CountBadge
            value={total}
            size="md"
            tone={hasHistory ? 'neutral' : 'muted'}
          />
        </div>
        <button
          onClick={() => router.push(`/projects/${projectId}/changes`)}
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            height: 30,
            padding: 0,
            fontSize: 12,
            color: T.text2,
            fontFamily: T.fontSans,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            transition: `color 200ms ${T.ease}`,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = T.text1;
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = T.text2;
          }}
        >
          View all
          <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
            <path
              d="M4 2l4 4-4 4"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>

      {/* Body — vertical timeline of recent commits.  Each commit is
          a flex row: a 16px gutter on the left holds a dot (and a
          line connecting to the next dot below), and the content
          column holds the message + relative time.
          We render the WHOLE block as `flex column` so the
          connecting line just stacks naturally between the
          dot of one row and the dot of the next — no SVG needed,
          which keeps the timeline trivially responsive to font-size
          / padding changes. */}
      <div style={{ padding: '14px 14px 12px', flex: 1 }}>
        {recent.length === 0 ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '32px 8px',
              color: T.text3,
              fontSize: 12,
            }}
          >
            No commits yet
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            {recent.map((c, idx) => {
              const isFirst = idx === 0;
              const isLast = idx === recent.length - 1;
              return (
                <button
                  key={c.commit_id}
                  onClick={() =>
                    router.push(`/projects/${projectId}/changes`)
                  }
                  title={c.message || '(no message)'}
                  style={{
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    padding: 0,
                    margin: 0,
                    width: '100%',
                    textAlign: 'left',
                    display: 'flex',
                    gap: 10,
                    fontFamily: T.fontSans,
                    color: T.text2,
                    transition: `color 160ms ${T.ease}`,
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.color = T.text1;
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.color = T.text2;
                  }}
                >
                  {/* Timeline gutter: dot at the top + a 1px line
                      that flexes to fill the remaining height.  The
                      LAST row gets no line so the timeline ends at
                      its final dot rather than dangling into
                      whitespace below. */}
                  <div
                    style={{
                      width: 12,
                      flexShrink: 0,
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                    }}
                  >
                    <span
                      aria-hidden
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: 1.5,
                        background: isFirst ? T.live : 'transparent',
                        border: isFirst
                          ? `1px solid ${T.live}`
                          : `1px solid ${T.text3}`,
                        boxSizing: 'border-box',
                        marginTop: 5,
                        flexShrink: 0,
                      }}
                    />
                    {!isLast && (
                      <span
                        aria-hidden
                        style={{
                          width: 1,
                          background: T.text4,
                          flex: 1,
                          marginTop: 2,
                          marginBottom: 2,
                        }}
                      />
                    )}
                  </div>

                  {/* Content column: message + relative-time stamp
                      stacked.  Bottom padding on every row except
                      the last gives the timeline its breathing
                      room. */}
                  <div
                    style={{
                      flex: 1,
                      minWidth: 0,
                      paddingBottom: isLast ? 0 : 10,
                    }}
                  >
                    <div
                      style={{
                        fontSize: 12,
                        color: 'inherit',
                        fontWeight: isFirst ? 500 : 400,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        lineHeight: 1.4,
                      }}
                    >
                      {c.message || '(no message)'}
                    </div>
                    {c.created_at && (
                      <div
                        style={{
                          fontSize: 11,
                          color: T.text3,
                          marginTop: 1,
                          fontVariantNumeric: 'tabular-nums',
                        }}
                      >
                        {formatRelative(c.created_at)}
                      </div>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer — full-width sparkline that visually anchors the
          card.  No top border on the spark area itself (it would cut
          the chart off from the timeline above and read as two
          separate sections); only the labels live below in a
          comfortable 12px gutter. */}
      <div
        style={{
          padding: '0 14px 12px',
        }}
      >
        <WideSparkline buckets={buckets} hasHistory={hasHistory} />
      </div>
    </div>
  );
}
