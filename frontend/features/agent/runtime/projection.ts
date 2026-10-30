import type { MessagePart } from '@/components/chat/types';
import { runActive, type AgentRun, type AgentTool } from './types';

export function projectRunMessages(runs: AgentRun[]) {
  return runs.flatMap(run => {
    const parts: MessagePart[] = (run.tools ?? []).map(tool => ({ type: 'tool', toolId: tool.call_id,
      toolName: tool.name, toolInput: JSON.stringify(tool.input),
      toolStatus: toolStatus(tool, runActive(run)),
      toolOutput: (tool.result?.content ?? []).filter(part => part.type === 'text').map(part => part.text ?? '').join('\n')
        || (tool.state === 'rejected' ? 'You declined this tool call. The Agent can continue with another approach.' : ''),
    }));
    if (run.snapshot.text) parts.push({ type: 'text', content: run.snapshot.text });
    return [
      { id: `${run.id}:user`, role: 'user' as const, content: run.prompt, timestamp: new Date(run.created_at), parts: undefined, isStreaming: false },
      { id: `${run.id}:assistant`, role: 'assistant' as const, content: run.snapshot.text ?? '',
        timestamp: new Date(run.updated_at), parts, isStreaming: runActive(run) },
    ];
  });
}

export function runStatus(run?: AgentRun): string | null {
  if (!run) return null;
  switch (run.state) {
    case 'queued': return 'Waiting to start';
    case 'waiting_approval': return 'Waiting for your approval';
    case 'publishing': return 'Uploading committed changes to Cloud';
    case 'stopped': return 'Stopped';
    case 'conflict': return 'The Cloud has newer changes. Your work is kept in this workspace.';
    case 'outcome_unknown': return run.snapshot.code === 'publication_outcome_unknown'
      ? 'Upload confirmation is unavailable. Your work is kept, and the Agent task will not repeat automatically.'
      : 'Result confirmation is unavailable. The Agent task will not repeat automatically.';
    case 'failed': return `Agent could not finish${run.snapshot.code ? ` (${run.snapshot.code})` : ''}.`;
    default: return null;
  }
}

function toolStatus(tool: AgentTool, active: boolean): MessagePart['toolStatus'] {
  if (tool.state === 'completed') return tool.result?.isError ? 'error' : 'completed';
  if (tool.state === 'rejected' || !active) return 'error';
  return 'running';
}
