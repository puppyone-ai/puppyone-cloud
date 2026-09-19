'use client';

import { useEffect, useState, useCallback } from 'react';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import { batchGetETLTaskStatus, isTerminalStatus } from '../lib/etlApi';

// 使用 sessionStorage，刷新后自动清空
const STORAGE_KEY = 'etl_pending_tasks';

/**
 * Stale-task cutoff: any task still in ``uploading`` after this
 * window is treated as orphaned (e.g. tab was closed mid-upload, or
 * the network died and the abort handler didn't get to run).
 *
 * 30 minutes is comfortably longer than any reasonable upload
 * (1 GB at ~10 Mbps takes ~14 min) but short enough that ghost
 * tasks don't pile up across browser sessions when sessionStorage
 * gets shared via window opener / SPA navigation.
 */
const STALE_UPLOADING_MS = 30 * 60 * 1000;

/**
 * Placeholder ID detection. We use placeholders during the direct-
 * to-S3 upload flow:
 *   - ``tmp-…``         — spawned by the widget the instant the user
 *                          drops a file, before /upload/init has run.
 *                          Swapped for the real ID via ``replaceTaskId``
 *                          once init returns.
 *   - ``placeholder-…`` — legacy IDs from older flows; still skipped
 *                          by the poller since they'd 404 the backend.
 *   - negative numerics — also legacy.
 *
 * Polling these would 404 the ``/tasks/batch`` endpoint and create
 * noise, so ``isPollable`` filters them out entirely.
 */
function isPlaceholderTaskId(taskId: string): boolean {
  return (
    taskId.startsWith('tmp-') ||
    taskId.startsWith('placeholder-') ||
    Number(taskId) < 0
  );
}

/**
 * 任务类型
 */
export type TaskType = 'file' | 'notion' | 'github' | 'airtable' | 'google_sheets' | 'google_docs' | 'linear' | 'gmail' | 'drive' | 'calendar';

/**
 * 任务记录（存储在 sessionStorage 中，刷新即消失）
 *
 * Lifecycle for a direct-to-S3 file upload:
 *   - ``uploading``   — client is PUTing parts to S3; ``progress``
 *     ticks 0..100. Polling skips this state because the client is
 *     authoritative.
 *   - ``finalizing``  — every part is up in S3, the client is
 *     waiting on ``/upload/complete`` to write the assembled bytes
 *     into the Version Engine. This is a CLIENT-driven temporary state; polling
 *     skips it so a transient backend ``pending`` snapshot can't
 *     regress the widget back to a worse-looking label. The
 *     transition out is always driven by ``onTaskCompleted`` /
 *     ``onTaskFailed`` from ``uploadApi``.
 *   - ``pending``     — legacy: enqueued for a worker. Direct-S3
 *     flow doesn't visit this state anymore (finalize is inline)
 *     but we keep it for SaaS connectors that still go through it.
 *   - ``processing``  — worker is doing OCR / LLM / writing versioned content
 *     (SaaS connectors only; direct-S3 finalize is inline).
 *   - ``completed`` / ``failed`` / ``cancelled`` — terminal; the
 *     widget stops animating and exposes a clear/cancel affordance.
 */
export interface PendingTask {
  taskId: string;
  projectId: string;
  tableId?: string;
  tableName?: string;
  filename: string;
  timestamp: number;
  taskType?: TaskType;
  status?:
    | 'uploading'
    | 'finalizing'
    | 'pending'
    | 'processing'
    | 'completed'
    | 'failed'
    | 'cancelled';
  /**
   * Upload progress 0..100. Populated only during the ``uploading``
   * phase; once we hand off to the worker the backend's task
   * progress takes over (which is coarser — 80, 100 — because
   * "writing versioned content" doesn't have meaningful sub-progress).
   */
  progress?: number;
  /** Last-seen error string for failed tasks. */
  error?: string;
}

/**
 * BackgroundTaskNotifier - 唯一的任务状态轮询器
 *
 * - 使用 sessionStorage（刷新后清空）
 * - 只在有非终态任务时轮询
 * - 所有任务终态后停止轮询
 */
export function BackgroundTaskNotifier() {
  const { session } = useAuth();
  const [pendingTasks, setPendingTasks] = useState<PendingTask[]>([]);

  // 调试用：全局清理函数
  useEffect(() => {
    (window as any).clearETLTasks = () => {
      sessionStorage.removeItem(STORAGE_KEY);
      setPendingTasks([]);
      window.dispatchEvent(new CustomEvent('etl-tasks-updated'));
      console.log('✅ Cleared all ETL tasks');
    };
    return () => {
      delete (window as any).clearETLTasks;
    };
  }, []);

  // 从 sessionStorage 加载任务
  const loadPendingTasks = useCallback(() => {
    try {
      const stored = sessionStorage.getItem(STORAGE_KEY);
      if (stored) {
        const tasks = JSON.parse(stored) as PendingTask[];
        setPendingTasks(tasks);
      } else {
        setPendingTasks([]);
      }
    } catch (error) {
      console.error('Failed to load pending tasks:', error);
      setPendingTasks([]);
    }
  }, []);

  /**
   * A task is "pollable" iff:
   *   - it has a real backend task_id (no legacy placeholders),
   *   - it's a file task (SaaS tasks don't expose /tasks/batch),
   *   - it's NOT in a CLIENT-authoritative state — ``uploading`` /
   *     ``finalizing``. During those phases the client knows more
   *     than the server (we're literally driving the state machine
   *     ourselves), so polling can only degrade the displayed
   *     status (e.g. backend says ``pending`` while we're showing
   *     ``finalizing``). The exit transitions for both come from
   *     ``uploadApi`` callbacks, not from polling.
   *   - it's not already terminal.
   */
  const isPollable = useCallback((t: PendingTask): boolean => {
    if (isPlaceholderTaskId(t.taskId)) return false;
    if (t.taskType && t.taskType !== 'file') return false;
    if (t.status === 'uploading' || t.status === 'finalizing') return false;
    const terminal =
      t.status === 'completed' ||
      t.status === 'failed' ||
      t.status === 'cancelled';
    return !terminal;
  }, []);

  const hasActiveTasksToPool = useCallback(() => {
    return pendingTasks.some(isPollable);
  }, [pendingTasks, isPollable]);

  const checkTaskStatus = useCallback(async () => {
    if (!session?.access_token || pendingTasks.length === 0) return;

    const etlTasks = pendingTasks.filter(isPollable);

    if (etlTasks.length === 0) return;

    let hasChanges = false;
    let updatedTasks = [...pendingTasks];

    try {
      const taskIds = etlTasks.map(t => t.taskId);
      const response = await batchGetETLTaskStatus(
        taskIds,
        session.access_token
      );

      updatedTasks = updatedTasks.map(pendingTask => {
        if (!isPollable(pendingTask)) return pendingTask;

        const task = response.tasks.find(t => t.task_id === pendingTask.taskId);
        if (task && pendingTask.status !== task.status) {
          hasChanges = true;

          if (isTerminalStatus(task.status)) {
            if (task.status === 'completed') {
              window.dispatchEvent(
                new CustomEvent('etl-task-completed', {
                  detail: {
                    taskId: task.task_id,
                    filename: pendingTask.filename,
                    tableId: pendingTask.tableId,
                  },
                })
              );
            } else if (task.status === 'failed') {
              window.dispatchEvent(
                new CustomEvent('etl-task-failed', {
                  detail: {
                    taskId: task.task_id,
                    error: task.error,
                    filename: pendingTask.filename,
                  },
                })
              );
            }
          }

          return { ...pendingTask, status: task.status };
        }
        return pendingTask;
      });
    } catch (error) {
      console.error('Failed to check ETL task status:', error);
    }

    if (hasChanges) {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(updatedTasks));
      setPendingTasks(updatedTasks);
      window.dispatchEvent(new CustomEvent('etl-tasks-updated'));
      window.dispatchEvent(new CustomEvent('projects-refresh'));
    }
  }, [session, pendingTasks]);

  // 初始加载和监听事件
  useEffect(() => {
    loadPendingTasks();

    const handleTasksUpdated = () => loadPendingTasks();
    window.addEventListener('etl-tasks-updated', handleTasksUpdated);
    return () =>
      window.removeEventListener('etl-tasks-updated', handleTasksUpdated);
  }, [loadPendingTasks]);

  // 轮询：只在有活跃任务时进行
  useEffect(() => {
    if (!hasActiveTasksToPool()) return;

    checkTaskStatus();
    const interval = setInterval(checkTaskStatus, 3000);
    return () => clearInterval(interval);
  }, [hasActiveTasksToPool, checkTaskStatus]);

  // ``beforeunload`` warning. If the user tries to close the tab /
  // navigate away while an upload is mid-flight, surface the
  // browser's native "Are you sure you want to leave?" dialog so we
  // get one last chance to keep them on the page until the bytes
  // land.
  //
  // Why this can't be more clever: modern browsers ignore any
  // custom message text and just show their generic "leave site?"
  // confirmation — the spec deliberately doesn't let a website
  // craft a misleading message. Setting ``returnValue`` is enough
  // to trigger the prompt; that's the entire API surface.
  //
  // Why we ALSO listen for ``etl-tasks-updated``: the listener
  // closes over the latest ``pendingTasks``, but updating that ref
  // while the listener is still attached would otherwise miss new
  // uploads added between renders. We re-attach on every change so
  // the warning sees current state.
  useEffect(() => {
    const hasInFlight = pendingTasks.some(
      (t) => t.status === 'uploading' || t.status === 'finalizing',
    );
    if (!hasInFlight) return;

    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [pendingTasks]);

  // Stale-task sweeper. Any task that has been ``uploading`` for
  // longer than STALE_UPLOADING_MS is almost certainly orphaned —
  // the user closed the tab mid-upload, the network died and the
  // catch handler in uploadApi never got to run, or the browser
  // killed our XHRs because of a tab freeze. Mark such tasks as
  // failed so the widget surfaces a stable terminal state instead
  // of spinning forever.
  //
  // Cleanup runs once on mount and then every 60s. We deliberately
  // don't do this on every state tick because it would trigger an
  // ``etl-tasks-updated`` storm on long upload runs.
  useEffect(() => {
    const sweep = () => {
      try {
        const stored = sessionStorage.getItem(STORAGE_KEY);
        if (!stored) return;
        const tasks = JSON.parse(stored) as PendingTask[];
        const now = Date.now();
        let changed = false;
        const cleaned = tasks.map(t => {
          // ``finalizing`` is a server-side wait window — finalize
          // shouldn't take 30 min for any sane file size, so if we've
          // been waiting that long the request was almost certainly
          // dropped (tab backgrounded long enough that the fetch
          // timed out, or the backend crashed mid-write). Mark it
          // failed so the widget converges to a stable terminal state
          // instead of spinning on a request that already returned.
          const stalledClientPhase =
            (t.status === 'uploading' || t.status === 'finalizing') &&
            now - t.timestamp > STALE_UPLOADING_MS;
          if (stalledClientPhase) {
            changed = true;
            return {
              ...t,
              status: 'failed' as const,
              error: t.error || 'Upload stalled — no progress in 30 minutes',
            };
          }
          return t;
        });
        if (changed) {
          sessionStorage.setItem(STORAGE_KEY, JSON.stringify(cleaned));
          window.dispatchEvent(new CustomEvent('etl-tasks-updated'));
        }
      } catch (e) {
        console.warn('[BackgroundTaskNotifier] stale sweep failed:', e);
      }
    };
    sweep();
    const interval = setInterval(sweep, 60_000);
    return () => clearInterval(interval);
  }, []);

  return null;
}

// ============= Helper Functions =============

/** 添加任务 */
export function addPendingTasks(tasks: Omit<PendingTask, 'timestamp'>[]) {
  const existing = JSON.parse(
    sessionStorage.getItem(STORAGE_KEY) || '[]'
  ) as PendingTask[];
  const newTasks = tasks.map(t => ({ ...t, timestamp: Date.now() }));
  sessionStorage.setItem(
    STORAGE_KEY,
    JSON.stringify([...existing, ...newTasks])
  );
  window.dispatchEvent(new CustomEvent('etl-tasks-updated'));
}

/** 通过 taskId 更新任务状态 */
export function updateTaskStatusById(
  taskId: string,
  status: PendingTask['status'],
  patch: Partial<Pick<PendingTask, 'progress' | 'error'>> = {},
) {
  const existing = JSON.parse(
    sessionStorage.getItem(STORAGE_KEY) || '[]'
  ) as PendingTask[];
  const updated = existing.map(t =>
    t.taskId === taskId ? { ...t, status, ...patch } : t
  );
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
  window.dispatchEvent(new CustomEvent('etl-tasks-updated'));
}

/**
 * Update a task's upload progress without firing the heavy
 * ``etl-tasks-updated`` event.
 *
 * Progress ticks during a multipart upload can fire dozens of times
 * per second per part. Re-broadcasting ``etl-tasks-updated`` on each
 * tick would re-run every component subscribed to the storage event
 * (sidebars, layout, etc.). We use a dedicated lightweight event
 * (``etl-task-progress``) that only the task widget listens for, so
 * we keep the rest of the UI quiet.
 */
export function updateTaskProgress(taskId: string, progress: number) {
  const existing = JSON.parse(
    sessionStorage.getItem(STORAGE_KEY) || '[]'
  ) as PendingTask[];
  let changed = false;
  const updated = existing.map(t => {
    if (t.taskId === taskId && t.progress !== progress) {
      changed = true;
      return { ...t, progress };
    }
    return t;
  });
  if (changed) {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
    window.dispatchEvent(
      new CustomEvent('etl-task-progress', {
        detail: { taskId, progress },
      }),
    );
  }
}

/**
 * Swap a placeholder ``taskId`` for the real one returned by
 * ``/upload/init``, preserving every other field on the task row
 * (status, progress, filename, etc.).
 *
 * Used by the direct-to-S3 upload flow: callers spawn a placeholder
 * row in ``onUploadStart`` (so the widget appears the instant the
 * user drops a file), then call this in ``onTaskCreated`` once the
 * backend's real ID is known. After this call all subsequent
 * progress / poll events flow against the real ID.
 *
 * No-ops if neither ID is currently present.
 */
export function replaceTaskId(oldId: string, newId: string) {
  if (oldId === newId) return;
  const existing = JSON.parse(
    sessionStorage.getItem(STORAGE_KEY) || '[]',
  ) as PendingTask[];
  let changed = false;
  const updated = existing.map((t) => {
    if (t.taskId === oldId) {
      changed = true;
      return { ...t, taskId: newId };
    }
    return t;
  });
  if (changed) {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
    window.dispatchEvent(new CustomEvent('etl-tasks-updated'));
  }
}

/** 通过 taskId 移除任务 */
export function removeTaskById(taskId: string) {
  const existing = JSON.parse(
    sessionStorage.getItem(STORAGE_KEY) || '[]'
  ) as PendingTask[];
  const filtered = existing.filter(t => t.taskId !== taskId);
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(filtered));
  window.dispatchEvent(new CustomEvent('etl-tasks-updated'));
}

/** 替换占位任务 */
export function replacePlaceholderTasks(
  tableId: string,
  realTasks: Omit<PendingTask, 'timestamp'>[]
) {
  const existing = JSON.parse(
    sessionStorage.getItem(STORAGE_KEY) || '[]'
  ) as PendingTask[];
  const filtered = existing.filter(
    t => !(t.tableId === tableId && isPlaceholderTaskId(t.taskId))
  );
  const newTasks = realTasks.map(t => ({ ...t, timestamp: Date.now() }));
  sessionStorage.setItem(
    STORAGE_KEY,
    JSON.stringify([...filtered, ...newTasks])
  );
  window.dispatchEvent(new CustomEvent('etl-tasks-updated'));
}

/** 移除失败文件的占位任务 */
export function removeFailedPlaceholders(tableId: string, filenames: string[]) {
  const existing = JSON.parse(
    sessionStorage.getItem(STORAGE_KEY) || '[]'
  ) as PendingTask[];
  const filtered = existing.filter(
    t =>
      !(t.tableId === tableId && isPlaceholderTaskId(t.taskId) && filenames.includes(t.filename))
  );
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(filtered));
  window.dispatchEvent(new CustomEvent('etl-tasks-updated'));
}

/** 移除 Table 的所有占位任务 */
export function removeAllPlaceholdersForTable(tableId: string) {
  const existing = JSON.parse(
    sessionStorage.getItem(STORAGE_KEY) || '[]'
  ) as PendingTask[];
  const filtered = existing.filter(
    t => !(t.tableId === tableId && isPlaceholderTaskId(t.taskId))
  );
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(filtered));
  window.dispatchEvent(new CustomEvent('etl-tasks-updated'));
}

/** 获取指定 table 的任务 */
export function getPendingFilesForTable(tableId: string): PendingTask[] {
  const stored = sessionStorage.getItem(STORAGE_KEY);
  if (!stored) return [];
  const tasks = JSON.parse(stored) as PendingTask[];
  return tasks.filter(t => t.tableId === tableId);
}

/** 获取所有任务 */
export function getAllPendingTasks(): PendingTask[] {
  const stored = sessionStorage.getItem(STORAGE_KEY);
  if (!stored) return [];
  return JSON.parse(stored) as PendingTask[];
}

/** 清空所有任务 */
export function clearAllTasks() {
  sessionStorage.removeItem(STORAGE_KEY);
  window.dispatchEvent(new CustomEvent('etl-tasks-updated'));
}

/** 获取正在处理中的 Table ID 列表 */
export function getProcessingTableIds(): Set<string> {
  const tasks = getAllPendingTasks();
  const tableIds = new Set<string>();

  tasks.forEach(task => {
    const isTerminal =
      task.status === 'completed' ||
      task.status === 'failed' ||
      task.status === 'cancelled';
    if (task.tableId && !isTerminal) {
      tableIds.add(task.tableId);
    }
  });

  return tableIds;
}
