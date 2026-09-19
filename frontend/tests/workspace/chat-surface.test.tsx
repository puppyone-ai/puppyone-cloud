import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentChatHeader } from '@/components/chat/AgentChatChrome';
import ChatInputArea from '@/components/chat/ChatInputArea';
import { ChatRuntimeView } from '@/components/agent/views/ChatRuntimeView';

const chat = vi.hoisted(() => ({
  sessions: [] as { id: string; title: string; agent_id: string; mode: null; created_at: string; updated_at: string }[],
  create: vi.fn(),
  send: vi.fn(),
  updateAgentInfo: vi.fn(),
  capabilities: new Set<string>(),
}));
vi.mock('@/contexts/AgentContext', () => ({ useAgent: () => ({
  currentAgentId: 'agent-1', savedAgents: [{ id: 'agent-1', name: 'Project Agent', type: 'chat', resources: [] }],
  selectedCapabilities: chat.capabilities, draftResources: [], updateAgentInfo: chat.updateAgentInfo,
}) }));
vi.mock('@/lib/hooks/useOnboarding', () => ({ useOnboarding: () => ({ completeStep: vi.fn() }) }));
vi.mock('@/lib/hooks/useChat', () => ({
  useChatSessions: () => ({ sessions: chat.sessions }),
  useChatMessages: () => ({ messages: [], isLoading: false }),
  createSession: chat.create, refreshChatSessions: vi.fn(), refreshChatMessages: vi.fn(),
}));
vi.mock('@/lib/chatApi', () => ({ sendChatMessage: chat.send }));

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  Element.prototype.scrollIntoView = vi.fn();
  chat.sessions = [];
  chat.capabilities.clear();
});

describe('Agent chat chrome', () => {
  it('restores a conversation from history and dismisses the popover', () => {
    const select = vi.fn();
    render(<AgentChatHeader title='New chat' onSelectSession={select} sessions={[{
      id: 'session-1', title: 'Review the project', agent_id: 'agent-1', mode: null,
      created_at: '2026-09-19T00:00:00Z', updated_at: '2026-09-19T00:00:00Z',
    }]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Chat history' }));
    fireEvent.click(screen.getByRole('button', { name: /Review the project/ }));
    expect(select).toHaveBeenCalledWith('session-1');
    expect(screen.queryByText('Review the project')).toBeNull();
  });

  it('closes history with Escape and returns keyboard focus', () => {
    render(<AgentChatHeader title='New chat' onSelectSession={vi.fn()} />);
    const trigger = screen.getByRole('button', { name: 'Chat history' });
    fireEvent.click(trigger);
    expect(screen.getByText('No chat history yet')).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByText('No chat history yet')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('keeps the rename form open and reports a failed save', async () => {
    const rename = vi.fn().mockRejectedValue(new Error('offline'));
    render(<AgentChatHeader title='Agent' agentName='Agent' onRename={rename} />);
    fireEvent.click(screen.getByRole('button', { name: 'Chat actions' }));
    fireEvent.click(screen.getByRole('button', { name: 'Rename agent' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Agent name' }), { target: { value: ' Research ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save agent name' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Could not rename'));
    expect(rename).toHaveBeenCalledWith('Research');
    expect(screen.getByRole('textbox', { name: 'Agent name' })).toBeTruthy();
  });

  it('prevents switching conversations during a response while allowing the panel to close', () => {
    const close = vi.fn();
    const newChat = vi.fn();
    render(<AgentChatHeader title='Agent' busy onNewChat={newChat} onSelectSession={vi.fn()} onClose={close} />);
    expect((screen.getByRole('button', { name: 'New chat' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Chat history' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Close chat panel' }));
    expect(close).toHaveBeenCalledOnce();
    expect(newChat).not.toHaveBeenCalled();
  });
});

describe('Agent composer', () => {
  const props = { inputValue: 'Draft', onInputChange: vi.fn(), onKeyDown: vi.fn(), onSend: vi.fn(),
    isLoading: false, showMentionMenu: false, filteredMentionOptions: [], mentionIndex: 0,
    onMentionSelect: vi.fn(), onMentionIndexChange: vi.fn() };

  it('does not allow sending from a disabled composer even with a draft', () => {
    render(<ChatInputArea {...props} disabled />);
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    expect(props.onSend).not.toHaveBeenCalled();
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(true);
  });

  it('preserves path selection through the mention menu', () => {
    render(<ChatInputArea {...props} showMentionMenu filteredMentionOptions={['project.name', 'project.files']} />);
    fireEvent.click(screen.getByRole('option', { name: '@project.files' }));
    expect(props.onMentionSelect).toHaveBeenCalledWith('project.files');
  });

  it('sends through the existing session API and renders the streamed response', async () => {
    chat.create.mockResolvedValue({ id: 'session-1' });
    const payload = new TextEncoder().encode('data: {"type":"text_delta","content":"Ready to help."}\n\ndata: [DONE]\n\n');
    chat.send.mockResolvedValue(new Response(new ReadableStream({ start(controller) {
      controller.enqueue(payload); controller.close();
    } })));
    render(<ChatRuntimeView availableTools={[]} />);
    const input = screen.getByRole('textbox', { name: 'Message Agent' });
    fireEvent.change(input, { target: { value: 'Summarize this project' } });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    expect(chat.send).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(screen.getByText('Ready to help.')).toBeTruthy());
    expect(chat.create).toHaveBeenCalledWith('agent-1', 'Summarize this project');
    expect(chat.send).toHaveBeenCalledWith('session-1', 'agent-1', 'Summarize this project', { activeToolIds: undefined });
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('');
  });
});
