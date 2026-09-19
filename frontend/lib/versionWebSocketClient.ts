/**
 * Version-engine WebSocket client — server-push commit_update consumer.
 *
 * One persistent WebSocket per (project_id, browser tab). The connection
 * stays open as long as any subscriber is registered; it auto-reconnects
 * with exponential backoff if the server drops it.
 *
 * **Note on the URL**: the path is ``/api/v1/version/{project_id}/ws``.
 * This is the product WebSocket surface for version notifications; Git
 * transport uses its own smart-HTTP routes.
 *
 * Auth contract (see ``ws_router.py``):
 *   The browser ``WebSocket`` constructor cannot set arbitrary headers,
 *   so we ship the JWT inside the ``Sec-WebSocket-Protocol`` field as
 *   ``version.bearer.<token>``. The server pulls the token off, then accepts
 *   the upgrade with a benign ``version.v1`` subprotocol so the JWT never
 *   appears in the response handshake (proxies log subprotocols).
 *
 * Frame contract:
 *   Every server-pushed frame is JSON. Currently the only kind is
 *   ``{type: "commit_update", ...}`` — see :class:`CommitUpdateEvent`.
 *   Clients that don't recognise a ``type`` should ignore the frame.
 */

import { getApiAccessToken } from './apiClient';
import { getProjectHead, getProjectHistory, type VersionCommitInfo } from './contentTreeApi';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:9090';

const _MIN_RECONNECT_DELAY_MS = 1_000;
const _MAX_RECONNECT_DELAY_MS = 30_000;
const _RECONNECT_JITTER_MS = 500;
/** Force a reconnect this many seconds before the JWT's ``exp``. The
 *  server checked auth at the upgrade handshake and never re-verifies,
 *  so a long-idle socket can technically outlive its token. Cycling
 *  early lets ``_connect`` pick up the fresh token supabase-js has
 *  already auto-refreshed. */
const _TOKEN_REFRESH_MARGIN_SECS = 60;
/** Hard cap on the recycle delay. If a token reports an absurdly far
 *  ``exp`` (or we fail to decode it), don't schedule a 30-day timer. */
const _MAX_TOKEN_LIFETIME_MS = 60 * 60 * 1000; // 1h
const _MAX_SEEN_EVENT_IDS = 2_048;
// Keep within the HTTP contract (1..100); larger pages receive 422 forever.
const _CATCH_UP_PAGE_SIZE = 100;

/**
 * Server → client commit_update frame. Mirrors
 * ``NotificationManager.broadcast_commit_update`` payload on the
 * backend. ``changed_files`` is the project-root-relative path list
 * extracted from the commit's ``changes`` log.
 */
export interface CommitUpdateEvent {
  type: 'commit_update';
  notification_id: string;
  scope: string;            // normalized path projection; '' = Project root
  commit_id: string;        // 40-hex SHA-1 git commit object hash
  pushed_by: string;        // agent identity, e.g. 'user:<uuid>'
  message: string;
  scope_hash: string;       // 40-hex SHA-1 of the new scope tree
  changed_files: string[];
  timestamp: string;        // ISO 8601 UTC
}

/** Anything else the server may push in the future. */
export interface UnknownEvent {
  type: string;
  [key: string]: unknown;
}

export type VersionNotification = CommitUpdateEvent | UnknownEvent;

export type VersionNotificationHandler = (event: VersionNotification) => void;

type ConnectionState = 'connecting' | 'open' | 'closed';

interface ProjectConnection {
  socket: WebSocket | null;
  state: ConnectionState;
  handlers: Set<VersionNotificationHandler>;
  reconnectAttempts: number;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  /** Scheduled timer that proactively recycles the socket before its
   *  upgrade-time JWT expires. Without this, a session left open for
   *  many hours holds a connection past the token's ``exp`` — the
   *  server accepted it at handshake and never re-verifies. Cleared
   *  on tear-down. */
  refreshTimer: ReturnType<typeof setTimeout> | null;
  /** Bumped on each ``connect()`` call so stale callbacks (from a
   *  socket whose lifetime ended) can detect they're obsolete and
   *  not schedule a reconnect on top of an already-replaced socket. */
  generation: number;
  /** Last canonical project head confirmed by either the history API or a
   * live frame. It is the exclusive anchor for reconnect catch-up. */
  canonicalHead: string;
  /** Frames arriving while canonical history is being pulled are held until
   * the snapshot has been applied. This closes the pull/subscribe race. */
  reconciling: boolean;
  pendingFrames: VersionNotification[];
  /** Insertion-ordered bounded sets. Keeping both identifiers is important:
   * a replayed commit has a synthetic notification id but is still the same
   * logical update as a buffered live frame. */
  seenNotificationIds: Map<string, true>;
  seenCommitIds: Map<string, true>;
}

const _connections = new Map<string, ProjectConnection>();

function _wsUrlFor(projectId: string): string {
  // Convert http(s):// → ws(s)://
  const httpBase = API_BASE_URL.replace(/\/$/, '');
  const wsBase = httpBase.replace(/^http(s?):\/\//i, (_m, s) => `ws${s}://`);
  return `${wsBase}/api/v1/version/${encodeURIComponent(projectId)}/ws`;
}

function _getOrCreate(projectId: string): ProjectConnection {
  let conn = _connections.get(projectId);
  if (!conn) {
    conn = {
      socket: null,
      state: 'closed',
      handlers: new Set(),
      reconnectAttempts: 0,
      reconnectTimer: null,
      refreshTimer: null,
      generation: 0,
      canonicalHead: '',
      reconciling: false,
      pendingFrames: [],
      seenNotificationIds: new Map(),
      seenCommitIds: new Map(),
    };
    _connections.set(projectId, conn);
  }
  return conn;
}

function _rememberBounded(set: Map<string, true>, value: string): boolean {
  if (!value) return false;
  if (set.has(value)) return true;
  set.set(value, true);
  while (set.size > _MAX_SEEN_EVENT_IDS) {
    const oldest = set.keys().next().value as string | undefined;
    if (oldest === undefined) break;
    set.delete(oldest);
  }
  return false;
}

function _dispatch(conn: ProjectConnection, event: VersionNotification): void {
  if (event.type === 'commit_update') {
    const commit = event as CommitUpdateEvent;
    const duplicateNotification = _rememberBounded(
      conn.seenNotificationIds,
      commit.notification_id,
    );
    const duplicateCommit = _rememberBounded(conn.seenCommitIds, commit.commit_id);
    if (duplicateNotification || duplicateCommit) return;
    conn.canonicalHead = commit.commit_id || conn.canonicalHead;
  }

  for (const h of conn.handlers) {
    try {
      h(event);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('[VersionWS] handler threw', err);
    }
  }
}

function _historyCommitEvent(commit: VersionCommitInfo): CommitUpdateEvent {
  return {
    type: 'commit_update',
    notification_id: `canonical:${commit.commit_id}`,
    scope: commit.scope_path || '',
    commit_id: commit.commit_id,
    pushed_by: commit.who || '',
    message: commit.message || '',
    scope_hash: commit.root_hash || '',
    changed_files: (commit.changes || []).map((change) => change.path),
    timestamp: commit.created_at || new Date(0).toISOString(),
  };
}

async function _reconcileCanonicalHistory(
  projectId: string,
  conn: ProjectConnection,
  generation: number,
): Promise<void> {
  const startingHead = conn.canonicalHead;
  let anchor = startingHead;

  try {
    // On the first connection we establish the canonical head without
    // replaying the project's entire history. On reconnect, page forward from
    // the last confirmed head until caught up; never silently truncate a long
    // disconnect window.
    if (!anchor) {
      const snapshot = await getProjectHead(projectId);
      if (conn.generation !== generation) return;
      conn.canonicalHead = snapshot.head_commit_id || '';
      if (conn.canonicalHead) {
        _rememberBounded(conn.seenCommitIds, conn.canonicalHead);
      }
    } else {
      for (;;) {
        const previousAnchor = anchor;
        const page = await getProjectHistory(projectId, _CATCH_UP_PAGE_SIZE, anchor);
        if (conn.generation !== generation) return;
        for (const commit of page.commits) {
          _dispatch(conn, _historyCommitEvent(commit));
          anchor = commit.commit_id || anchor;
        }
        if (page.commits.length < _CATCH_UP_PAGE_SIZE) break;
        if (!page.commits.length || anchor === previousAnchor) {
          throw new Error('canonical history pagination made no progress');
        }
      }
    }
  } catch (err) {
    // A socket must not be marked usable when the authoritative catch-up
    // failed: close it and retry the whole handshake/reconciliation. Keeping
    // it open here would recreate the original silent event-loss bug.
    // eslint-disable-next-line no-console
    console.error('[VersionWS] canonical reconciliation failed', err);
    if (conn.generation === generation && conn.socket) {
      conn.socket.close(1012, 'canonical-reconciliation-failed');
    }
    return;
  }

  if (conn.generation !== generation) return;
  conn.reconciling = false;
  const pending = conn.pendingFrames.splice(0);
  for (const event of pending) _dispatch(conn, event);
}

function _backoffDelayMs(attempt: number): number {
  const base = Math.min(
    _MAX_RECONNECT_DELAY_MS,
    _MIN_RECONNECT_DELAY_MS * Math.pow(2, attempt),
  );
  return base + Math.random() * _RECONNECT_JITTER_MS;
}

/** Pull the ``exp`` claim out of a JWT without verifying. We trust
 *  it because we (a) just got it from supabase-js's session storage,
 *  and (b) only use it to decide *when to recycle* — server-side auth
 *  is what actually enforces validity. Returns ``null`` if the token
 *  is malformed; the caller falls back to ``_MAX_TOKEN_LIFETIME_MS``. */
function _decodeJwtExpMs(token: string): number | null {
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    // Base64url → base64 → utf-8 JSON. ``atob`` is fine for ASCII
    // claims; if anyone ever puts unicode in ``exp`` we have bigger
    // problems.
    const payload = JSON.parse(
      atob(parts[1].replaceAll('-', '+').replaceAll('_', '/')),
    );
    return typeof payload.exp === 'number' ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}

function _scheduleTokenRefresh(
  projectId: string,
  conn: ProjectConnection,
  token: string,
): void {
  if (conn.refreshTimer) {
    clearTimeout(conn.refreshTimer);
    conn.refreshTimer = null;
  }
  const expMs = _decodeJwtExpMs(token);
  let delayMs: number;
  if (expMs === null) {
    // Couldn't decode — fall back to "recycle after the cap" so we
    // never hold a connection forever on a stale token.
    delayMs = _MAX_TOKEN_LIFETIME_MS;
  } else {
    const remainingMs = expMs - Date.now() - _TOKEN_REFRESH_MARGIN_SECS * 1000;
    delayMs = Math.min(_MAX_TOKEN_LIFETIME_MS, Math.max(60_000, remainingMs));
  }
  const myGen = conn.generation;
  conn.refreshTimer = setTimeout(() => {
    if (conn.generation !== myGen) return; // a newer connect already replaced us
    conn.refreshTimer = null;
    if (conn.socket) {
      // ``onclose`` will schedule the reconnect, which in turn calls
      // ``getApiAccessToken()`` to pick up the post-refresh JWT.
      try {
        conn.socket.close(1000, 'token-refresh');
      } catch {
        /* ignore */
      }
    }
  }, delayMs);
}

async function _connect(projectId: string, conn: ProjectConnection): Promise<void> {
  // Only attempt if there are still subscribers.
  if (conn.handlers.size === 0) {
    conn.state = 'closed';
    return;
  }

  // Claim the connection before awaiting auth: concurrent subscribers must not
  // open parallel sockets, and teardown during auth must invalidate this task.
  const myGen = ++conn.generation;
  conn.state = 'connecting';
  let token: string | null;
  try { token = await getApiAccessToken(); }
  catch { token = null; }
  if (conn.generation !== myGen || conn.handlers.size === 0) return;
  if (!token) {
    // Without a token the upgrade will be 1008'd. Schedule a retry —
    // user may be in the middle of a session refresh.
    _scheduleReconnect(projectId, conn);
    return;
  }

  let socket: WebSocket;
  try {
    socket = new WebSocket(_wsUrlFor(projectId), [`version.bearer.${token}`]);
  } catch (err) {
    // URL parse failure or insecure-context constraint — log and back off.
    // eslint-disable-next-line no-console
    console.error('[VersionWS] failed to construct WebSocket', err);
    _scheduleReconnect(projectId, conn);
    return;
  }

  conn.socket = socket;

  socket.onopen = () => {
    if (conn.generation !== myGen) return;  // stale callback
    conn.state = 'open';
    conn.reconnectAttempts = 0;
    conn.reconciling = true;
    // Schedule a forced reconnect ~60s before this token expires so
    // the next handshake picks up the supabase-js-refreshed JWT,
    // closing the "long-idle socket outlives its token" gap. Tied to
    // ``myGen`` so a teardown / re-open cycle replaces the timer.
    _scheduleTokenRefresh(projectId, conn, token);
    void _reconcileCanonicalHistory(projectId, conn, myGen);
  };

  socket.onmessage = (ev) => {
    if (conn.generation !== myGen) return;
    let parsed: VersionNotification;
    try {
      parsed = JSON.parse(ev.data) as VersionNotification;
    } catch {
      // Server should only send JSON; bad frame is a server bug.
      // eslint-disable-next-line no-console
      console.warn('[VersionWS] dropping non-JSON frame', ev.data);
      return;
    }
    if (conn.reconciling) conn.pendingFrames.push(parsed);
    else _dispatch(conn, parsed);
  };

  socket.onerror = () => {
    // ``onerror`` always precedes ``onclose``; we do the actual
    // reconnect bookkeeping in ``onclose`` to avoid double-scheduling.
  };

  socket.onclose = () => {
    if (conn.generation !== myGen) return;
    conn.socket = null;
    conn.state = 'closed';
    if (conn.handlers.size > 0) {
      _scheduleReconnect(projectId, conn);
    }
  };
}

function _scheduleReconnect(projectId: string, conn: ProjectConnection): void {
  if (conn.reconnectTimer) return;
  const delay = _backoffDelayMs(conn.reconnectAttempts);
  conn.reconnectAttempts += 1;
  conn.reconnectTimer = setTimeout(() => {
    conn.reconnectTimer = null;
    void _connect(projectId, conn);
  }, delay);
}

function _teardown(projectId: string, conn: ProjectConnection): void {
  if (conn.reconnectTimer) {
    clearTimeout(conn.reconnectTimer);
    conn.reconnectTimer = null;
  }
  if (conn.refreshTimer) {
    clearTimeout(conn.refreshTimer);
    conn.refreshTimer = null;
  }
  conn.generation += 1;  // invalidate any in-flight callbacks
  if (conn.socket) {
    try {
      conn.socket.close(1000, 'no subscribers');
    } catch {
      /* ignore */
    }
    conn.socket = null;
  }
  conn.state = 'closed';
  conn.reconnectAttempts = 0;
  conn.reconciling = false;
  conn.pendingFrames = [];
  _connections.delete(projectId);
}

/**
 * Subscribe a handler for ``commit_update`` (and any other future
 * server-push event) on this project. Returns an ``unsubscribe`` fn.
 *
 * The first subscribe per project opens the underlying socket; the
 * last unsubscribe tears it down. Concurrent subscribers share one
 * socket — handlers are independently isolated.
 */
export function subscribeVersionNotifications(
  projectId: string,
  handler: VersionNotificationHandler,
): () => void {
  if (!projectId) {
    // Defensive: no-op subscription for un-mounted / loading state.
    return () => {};
  }

  const conn = _getOrCreate(projectId);
  conn.handlers.add(handler);

  if (conn.state === 'closed') {
    void _connect(projectId, conn);
  }

  return () => {
    conn.handlers.delete(handler);
    if (conn.handlers.size === 0) {
      _teardown(projectId, conn);
    }
  };
}

/**
 * Test / hot-reload helper — closes every active connection. Not
 * meant for app-runtime use.
 */
export function _resetAllForTests(): void {
  for (const [pid, conn] of Array.from(_connections.entries())) {
    _teardown(pid, conn);
  }
  _connections.clear();
}
