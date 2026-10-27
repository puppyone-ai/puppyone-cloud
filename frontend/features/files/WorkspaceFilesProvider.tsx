'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import useSWR, {mutate} from 'swr';
import { useAuth } from '@/contexts/SupabaseAuthProvider';
import { apiRawRequest, apiStreamRequest } from '@/lib/apiClient';
import { createWorkspaceFiles, publishedFiles, workspaceEndpoint, workspaceStatus } from '@/lib/workspaceFiles';
import { WorkspaceFilesContext as Context } from '@/lib/hooks/useWorkspaceFiles';

function workspaceMessage(state: string) {
  if (state === 'retained' || state === 'cleanup_pending') return 'Your workspace needs recovery before you can edit again.';
  if (state === 'paused') return 'Your workspace is paused.';
  if (state === 'retired') return 'Open your workspace to start editing.';
  return 'Preparing your workspace…';
}

async function readEvents(response: Response, signal: AbortSignal, event: (text: string) => boolean) {
  if (!response.ok || !response.body) throw new Error('Workspace updates unavailable');
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffered = '';
  try {
    while (!signal.aborted) {
      const {done, value} = await reader.read();
      if (done) return;
      buffered += decoder.decode(value, {stream: true});
      if (buffered.length > 32768) throw new Error('Invalid workspace update');
      let end;
      while ((end = buffered.indexOf('\n\n')) >= 0) {
        const frame = buffered.slice(0, end); buffered = buffered.slice(end + 2);
        if (!event(frame)) return;
      }
    }
  } finally { await reader.cancel().catch(() => {}); }
}

export function WorkspaceFilesProvider({projectId, children}: {projectId: string; children: ReactNode}) {
  const {userId} = useAuth();
  const key = userId ? ['live-workspace', userId, projectId] : null;
  const {data, error, mutate: refresh} = useSWR(key, () => workspaceStatus(projectId), {
    refreshInterval: 3000, revalidateOnFocus: true, keepPreviousData: false,
  });
  const workspace = data?.workspace ?? null;
  const [revision, setRevision] = useState(0);
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState<string>();
  const identity = workspace ? `${userId}:${workspace.id}:${workspace.generation}` : 'published';
  const files = useMemo(() => userId && workspace?.project_id === projectId ? createWorkspaceFiles(workspace) : publishedFiles,
    [workspace, userId, projectId]);

  const generation = workspace?.generation;
  const running = workspace?.state === 'running';
  const canResume = workspace?.state === 'paused' || workspace?.state === 'retired';
  useEffect(() => {
    if (!generation || !running) return;
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout>;
    const invalidate = () => {
      setRevision(value => value + 1);
      void mutate(key => Array.isArray(key) && ['tree', 'table'].includes(key[0]) && key[1] === projectId);
    };
    const connect = async () => {
      try {
        const query = new URLSearchParams({generation});
        const response = await apiStreamRequest(`${workspaceEndpoint(projectId)}/events?${query}`, {signal: controller.signal});
        await readEvents(response, controller.signal, frame => {
          if (frame.startsWith('event: change')) invalidate();
          if (frame.startsWith('event: unavailable')) { void refresh(); return false; }
          return true;
        });
      } catch { if (!controller.signal.aborted) void refresh(); }
      if (!controller.signal.aborted) retry = setTimeout(() => { void connect(); }, 3000);
    };
    void connect();
    return () => { controller.abort(); clearTimeout(retry); };
  }, [identity, generation, running, projectId, refresh]);

  async function resume() {
    setOpening(true); setOpenError(undefined);
    try { await apiRawRequest(workspaceEndpoint(projectId), {method: 'POST', timeoutMs: 180000}); await refresh(); }
    catch (error) { setOpenError(error instanceof Error ? error.message : String(error)); }
    finally { setOpening(false); }
  }
  return <Context.Provider value={{files, identity, revision, ready: Boolean(data) && !error, workspace}}>
    {workspace && workspace.state !== 'running' && <div role="status" className="px-4 py-2 text-sm">
      {workspaceMessage(workspace.state)}
      {canResume && <button onClick={() => { void resume(); }} disabled={opening}>
        {opening ? 'Opening…' : 'Resume workspace'}
      </button>}{openError && <span role="alert"> {openError}</span>}
    </div>}
    {error && <div role="alert">Could not load workspace. <button onClick={() => { void refresh(); }}>Retry</button></div>}
    {children}
  </Context.Provider>;
}
