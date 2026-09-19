'use client';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import BotMessage from '../../chat/BotMessage';
import UserMessage from '../../chat/UserMessage';
import ChatInputArea, {
  ChatInputAreaRef,
  type AccessOption,
} from '../../chat/ChatInputArea';
import {
  useChatSessions,
  useChatMessages,
  refreshChatSessions,
  refreshChatMessages,
  createSession,
  type MessagePart,
} from '../../../lib/hooks/useChat';
import { sendChatMessage } from '../../../lib/chatApi';
import { useMention } from '../../../lib/hooks/useMention';
import { useAgent } from '@/contexts/AgentContext';
import { useOnboarding } from '@/lib/hooks/useOnboarding';
import { Dots } from '@/components/loading';
import { AgentChatBrand, AgentChatEmptyState, AgentChatHeader } from '@/components/chat/AgentChatChrome';
import chatStyles from '@/components/chat/AgentChatSurface.module.css';

import { type Tool as DbTool } from '../../../lib/mcpApi';

type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

interface Message {
  id?: string;
  role: MessageRole;
  content: string;
  timestamp?: Date;
  parts?: MessagePart[];
  isStreaming?: boolean;
}

interface ChatRuntimeViewProps {
  availableTools: AccessOption[];
  tableData?: unknown;
  tableId?: number | string;
  projectId?: number | string;
  onDataUpdate?: (newData: unknown) => void;
  projectTools?: DbTool[];
  onClose?: () => void;
  onBack?: () => void;
  seamlessHeader?: boolean;
}

export function ChatRuntimeView({
  availableTools,
  tableData,
  tableId,
  projectId,
  onDataUpdate,
  projectTools,
  onClose,
  onBack,
  seamlessHeader = false,
}: ChatRuntimeViewProps) {
  const {
    selectedCapabilities,
    toggleCapability,
    currentAgentId,
    savedAgents,
    draftResources,
    addDraftResource,
    updateDraftResource,
    removeDraftResource,
    setDraftResources,
    deleteAgent,
    updateAgentInfo,
    updateAgentResources,
  } = useAgent();

  const currentAgent = currentAgentId ? savedAgents.find(a => a.id === currentAgentId) : null;
  const agentName = currentAgent ? currentAgent.name : 'Agent';

  const { completeStep } = useOnboarding();

  // --- Local State ---
  const [inputValue, setInputValue] = useState('');
  const [isSettingsExpanded, setIsSettingsExpanded] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const inputAreaRef = useRef<ChatInputAreaRef>(null);
  const isInitialScrollRef = useRef(true); // Track if this is initial load or agent switch

  // 编辑 agent 信息
  const [editingName, setEditingName] = useState('');

  // 保存资源配置的状态
  const [isSavingResources, setIsSavingResources] = useState(false);

  // 检测资源是否有更改
  const hasResourceChanges = React.useMemo(() => {
    if (!currentAgent?.resources) return draftResources.length > 0;
    if (draftResources.length !== currentAgent.resources.length) return true;
    return draftResources.some((draft, i) => {
      const original = currentAgent.resources![i];
      return draft.path !== original.path ||
             (draft.readonly ?? true) !== (original.readonly ?? true);
    });
  }, [draftResources, currentAgent?.resources]);

  // 保存资源权限
  const handleSaveResources = useCallback(async () => {
    if (!currentAgentId || !hasResourceChanges) return;
    setIsSavingResources(true);
    try {
      await updateAgentResources(currentAgentId, draftResources);
    } catch (error) {
      console.error('Failed to save resources:', error);
      alert('Failed to save resource permissions. Please try again.');
    } finally {
      setIsSavingResources(false);
    }
  }, [currentAgentId, draftResources, hasResourceChanges, updateAgentResources]);

  // 当展开设置面板时，初始化编辑值
  useEffect(() => {
    if (isSettingsExpanded && currentAgent) {
      setEditingName(currentAgent.name);
      // 同步资源数据到 draftResources，以便编辑
      if (currentAgent.resources) {
        setDraftResources([...currentAgent.resources]);
      } else {
        setDraftResources([]);
      }
    }
  }, [isSettingsExpanded, currentAgent, setDraftResources]);

  // Database state
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [isNewChatMode, setIsNewChatMode] = useState(false); // 标记用户是否主动点击了"新建聊天"
  const prevAgentIdRef = useRef<string | null>(null);

  // @ mention Hook
  const mention = useMention({ data: tableData });

  // Database Hooks - 按 agent 过滤 session
  const { sessions } = useChatSessions(currentAgentId);
  const { messages: dbMessages, isLoading: messagesLoading } = useChatMessages(currentSessionId);

  const prevSessionIdRef = useRef<string | null>(null);
  const hasLoadedForSessionRef = useRef<string | null>(null);

  // 当切换 agent 时，重置 session
  useEffect(() => {
    if (prevAgentIdRef.current !== currentAgentId) {
      // Agent 切换了，重置 session
      setCurrentSessionId(null);
      setMessages([]);
      setIsNewChatMode(false); // 切换 agent 时重置新建聊天模式
      prevSessionIdRef.current = null;
      hasLoadedForSessionRef.current = null;
      prevAgentIdRef.current = currentAgentId;
      isInitialScrollRef.current = true; // Reset scroll behavior for new agent
    }
  }, [currentAgentId]);

  // 当 sessions 加载后，自动选择最新的 session（但如果用户主动新建聊天则不自动选择）
  useEffect(() => {
    if (sessions.length > 0 && currentSessionId === null && !isNewChatMode) {
      // 选择最新的 session（第一个）
      setCurrentSessionId(sessions[0].id);
    }
  }, [sessions, currentSessionId, isNewChatMode]);

  // Sync dbMessages to local messages (on session switch only)
  useEffect(() => {
    const sessionId = currentSessionId;
    if (!sessionId) {
      if (prevSessionIdRef.current !== null) {
        setMessages([]);
      }
      prevSessionIdRef.current = null;
      hasLoadedForSessionRef.current = null;
      return;
    }

    if (sessionId !== prevSessionIdRef.current) {
      hasLoadedForSessionRef.current = null;
      prevSessionIdRef.current = sessionId;
      setMessages([]);
    }

    if (messagesLoading) return;
    if (hasLoadedForSessionRef.current === sessionId) return;

    hasLoadedForSessionRef.current = sessionId;

    if (dbMessages && dbMessages.length > 0) {
      const localMessages: Message[] = dbMessages.map(m => ({
        id: m.id,
        role: m.role as MessageRole,
        content: m.content || '',
        parts: m.parts || undefined,
        timestamp: new Date(m.created_at),
      }));
      setMessages(localMessages);
    } else {
      setMessages([]);
    }
  }, [currentSessionId, dbMessages, messagesLoading]);

  // Auto-scroll
  useEffect(() => {
    if (isInitialScrollRef.current) {
      // Initial load or agent switch: jump instantly without animation
      messagesEndRef.current?.scrollIntoView({ behavior: 'instant' });
      // After first scroll, use smooth scrolling for subsequent updates
      if (messages.length > 0) {
        isInitialScrollRef.current = false;
      }
    } else {
      // New message arrived: smooth scroll
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [
    messages.length,
    messages[messages.length - 1]?.content,
    messages[messages.length - 1]?.parts?.length,
  ]);

  // Send message — explicit session creation, all via backend API
  const handleSend = useCallback(async () => {
    if (!inputValue.trim() || isLoading || !currentAgentId) return;

    const currentInput = inputValue;
    setInputValue('');
    setIsLoading(true);

    const userMessage: Message = {
      role: 'user',
      content: currentInput,
      timestamp: new Date(),
    };
    setMessages(prev => [...prev, userMessage]);
    completeStep('chat');

    if (abortControllerRef.current) abortControllerRef.current.abort();
    abortControllerRef.current = new AbortController();

    setMessages(prev => [
      ...prev,
      {
        role: 'assistant',
        content: '',
        timestamp: new Date(),
        parts: [],
        isStreaming: true,
      },
    ]);

    let effectiveSessionId: string | null = currentSessionId;

    try {
      // Step 1: Ensure session exists (create explicitly if needed)
      if (!effectiveSessionId) {
        const session = await createSession(currentAgentId, currentInput);
        effectiveSessionId = session.id;
        prevSessionIdRef.current = session.id;
        hasLoadedForSessionRef.current = session.id;
        setCurrentSessionId(session.id);
        setIsNewChatMode(false);
      }

      // Step 2: Collect active tool IDs
      const activeToolIds: string[] = [];
      for (const optionId of selectedCapabilities) {
        const match = optionId.match(/^tool:(.+)$/);
        if (match) {
          activeToolIds.push(match[1]);
        }
      }

      // Auto-complete 'chat' onboarding step on first message sent
      try {
        const KEY = 'puppyone_onboarding_v1';
        const state = JSON.parse(localStorage.getItem(KEY) || '{"hasSeenWelcome":true,"completedSteps":[],"dismissedChecklist":false}');
        if (!state.completedSteps.includes('chat')) {
          state.completedSteps.push('chat');
          localStorage.setItem(KEY, JSON.stringify(state));
        }
      } catch {}

      // Step 3: Send message via SSE (backend handles persistence)
      const response = await sendChatMessage(
        effectiveSessionId,
        currentAgentId,
        currentInput,
        { activeToolIds: activeToolIds.length > 0 ? activeToolIds : undefined },
      );

      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) throw new Error('No response body');

      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6).trim();
          if (!data || data === '[DONE]') continue;

          try {
            const event = JSON.parse(data);

            // Skip session event (we already created the session explicitly)
            if (event.type === 'session') continue;

            setMessages(prev => {
              const newMessages = [...prev];
              const last = newMessages[newMessages.length - 1];
              if (!last || last.role !== 'assistant') return prev;

              const parts = [...(last.parts || [])];

              switch (event.type) {
                case 'status':
                  break;
                case 'tool_start':
                  parts.push({
                    type: 'tool',
                    toolId: String(event.toolId),
                    toolName: event.toolName || 'tool',
                    toolInput: event.toolInput,
                    toolStatus: 'running',
                  });
                  break;
                case 'tool_end': {
                  const toolIdx = parts.findIndex(p => p.toolId === String(event.toolId));
                  if (toolIdx !== -1) {
                    parts[toolIdx] = {
                      ...parts[toolIdx],
                      toolStatus: event.success ? 'completed' : 'error',
                      toolOutput: event.output,
                    };
                  }
                  break;
                }
                case 'text':
                  parts.push({ type: 'text', content: event.content });
                  break;
                case 'text_delta': {
                  const lastPart = parts[parts.length - 1];
                  if (lastPart && lastPart.type === 'text') {
                    parts[parts.length - 1] = {
                      ...lastPart,
                      content: (lastPart.content || '') + event.content,
                    };
                  } else {
                    parts.push({ type: 'text', content: event.content });
                  }
                  break;
                }
                case 'result':
                  if ((event.updatedNodes || event.updatedData) && onDataUpdate) {
                    onDataUpdate(event.updatedNodes || event.updatedData);
                  }
                  break;
                case 'error':
                  parts.push({ type: 'text', content: `Error: ${event.message}` });
                  break;
              }

              const content = parts
                .filter(p => p.type === 'text')
                .map(p => p.content)
                .join('\n\n');
              return [...newMessages.slice(0, -1), { ...last, content, parts }];
            });
          } catch {}
        }
      }

      // Mark stream as complete
      setMessages(prev => {
        const newMessages = [...prev];
        const last = newMessages[newMessages.length - 1];
        if (last?.role === 'assistant') {
          const parts = [...(last.parts || [])];
          parts.forEach((p, i) => {
            if (p.type === 'tool' && p.toolStatus === 'running') {
              parts[i] = { ...p, toolStatus: 'completed' };
            }
          });
          last.parts = parts;
          last.isStreaming = false;
        }
        return newMessages;
      });

      // Refresh from DB to get persisted state
      if (effectiveSessionId) {
        refreshChatMessages(effectiveSessionId);
        refreshChatSessions(currentAgentId);
      }
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') return;
      setMessages(prev => {
        const newMessages = [...prev];
        const last = newMessages[newMessages.length - 1];
        if (last?.role === 'assistant') {
          const parts = [...(last.parts || [])];
          parts.forEach((p, i) => {
            if (p.type === 'tool' && p.toolStatus === 'running') {
              parts[i] = { ...p, toolStatus: 'error' };
            }
          });
          parts.push({ type: 'text', content: 'An error occurred, please try again.' });
          last.content = 'An error occurred, please try again.';
          last.parts = parts;
          last.isStreaming = false;
        }
        return newMessages;
      });
    } finally {
      setIsLoading(false);
      abortControllerRef.current = null;
    }
  }, [
    inputValue,
    isLoading,
    currentAgentId,
    currentSessionId,
    selectedCapabilities,
    onDataUpdate,
  ]);

  // Input handling with mention
  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      mention.handleInputChange(e, inputValue, setInputValue);
    },
    [mention, inputValue]
  );

  const handleSelectMention = useCallback(
    (key: string) => {
      mention.handleSelectMention(key, inputValue, setInputValue, inputAreaRef.current);
    },
    [mention, inputValue]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.nativeEvent.isComposing) return;

      if (mention.showMentionMenu && mention.filteredMentionOptions.length > 0) {
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          handleSelectMention(mention.filteredMentionOptions[mention.mentionIndex]);
          return;
        }
      }
      mention.handleKeyDown(e, handleSend);
    },
    [mention, handleSelectMention, handleSend]
  );

  // 新建聊天会话
  const handleNewChat = useCallback(() => {
    setCurrentSessionId(null);
    setMessages([]);
    setIsNewChatMode(true); // 标记为新建聊天模式，防止自动选择最新 session
  }, []);

  const isEmpty = messages.length === 0 && !messagesLoading;

  return (
    <div className={chatStyles.surface} data-seamless-header={seamlessHeader || undefined}>
      <AgentChatHeader
        title={sessions.find(session => session.id === currentSessionId)?.title || agentName}
        agentName={agentName}
        sessions={sessions}
        currentSessionId={currentSessionId}
        busy={isLoading}
        onSelectSession={currentAgentId ? setCurrentSessionId : undefined}
        onNewChat={currentAgentId ? handleNewChat : undefined}
        onSettings={currentAgentId ? () => setIsSettingsExpanded(value => !value) : undefined}
        onRename={currentAgentId ? name => updateAgentInfo(currentAgentId, name, currentAgent?.icon || '') : undefined}
        onClose={onClose}
        onBack={onBack}
      />

      {/* Expandable Settings Panel */}
      {isSettingsExpanded && currentAgent && (
        <div className={chatStyles.settings} style={{
          padding: '12px 16px',
          background: 'var(--po-panel-raised)',
          borderBottom: '1px solid var(--po-border-subtle)',
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}>
          {/* 编辑名字和图标 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {/* Type icon — fixed, non-editable */}
            <span style={{
              width: 32, height: 32, borderRadius: '50%',
              background: 'var(--po-panel)', border: '1px solid var(--po-border)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0, color: 'var(--po-text-subtle)',
            }}>
              <AgentChatBrand />
            </span>

            {/* 名字输入 */}
            <input
              type="text"
              value={editingName}
              onChange={(e) => setEditingName(e.target.value)}
              style={{
                flex: 1,
                height: 32,
                background: 'var(--po-panel)',
                border: '1px solid var(--po-border)',
                borderRadius: 4,
                padding: '0 10px',
                color: 'var(--po-text)',
                fontSize: 14,
                outline: 'none',
              }}
              onFocus={e => e.currentTarget.style.borderColor = 'var(--po-success)'}
              onBlur={e => e.currentTarget.style.borderColor = 'var(--po-border)'}
            />

            {/* 保存按钮 */}
            <button
              onClick={async () => {
                if (currentAgentId && editingName.trim()) {
                  await updateAgentInfo(currentAgentId, editingName.trim(), currentAgent.icon || '');
                }
              }}
              disabled={!editingName.trim() || editingName === currentAgent.name}
              style={{
                height: 32,
                padding: '0 12px',
                background: editingName.trim() && editingName !== currentAgent.name ? 'var(--po-success)' : 'var(--po-border)',
                color: editingName.trim() && editingName !== currentAgent.name ? 'var(--po-text-inverse)' : 'var(--po-text-disabled)',
                border: 'none',
                borderRadius: 4,
                cursor: editingName.trim() && editingName !== currentAgent.name ? 'pointer' : 'not-allowed',
                fontSize: 13,
                fontWeight: 500,
                transition: 'all 0.15s',
              }}
            >
              Save
            </button>
          </div>

          {/* Agent's bash access - 和 AgentSettingView 保持一致 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 6 }}>
            <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--po-text-subtle)' }}>{"Agent's bash access"}</span>
          </div>
          <div
            style={{
              minHeight: 88,
              background: 'transparent',
              border: '1px dashed var(--po-border)',
              borderRadius: 6,
              transition: 'all 0.15s',
            }}
            onDragOver={(e) => {
              e.preventDefault();
              e.currentTarget.style.borderColor = 'var(--po-success)';
              e.currentTarget.style.background = 'color-mix(in srgb, var(--po-success) 4%, transparent)';
            }}
            onDragLeave={(e) => {
              e.currentTarget.style.borderColor = 'var(--po-border)';
              e.currentTarget.style.background = 'transparent';
            }}
            onDrop={(e) => {
              e.preventDefault();
              e.currentTarget.style.borderColor = 'var(--po-border)';
              e.currentTarget.style.background = 'transparent';
              try {
                const data = e.dataTransfer.getData('application/json');
                if (data) {
                  const node = JSON.parse(data);
                  const isFolder = node.type === 'folder';
                  const isJson = node.type === 'json';
                  addDraftResource({
                    path: node.nodeId || node.id,
                    nodeName: node.name,
                    nodeType: isFolder ? 'folder' : (isJson ? 'json' : 'file'),
                    readonly: false, // 默认 Write 模式
                  });
                }
              } catch (err) {
                console.error('Drop failed', err);
              }
            }}
          >
            {/* 文件列表 */}
            <div style={{ padding: draftResources.length > 0 ? 6 : 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
              {draftResources.map(resource => {
                const isReadonly = resource.readonly ?? true;
                return (
                  <div
                    key={resource.path}
                    style={{
                      height: 32,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '0 10px',
                      borderRadius: 4,
                      background: 'var(--po-panel-raised)',
                      border: '1px solid var(--po-border-strong)',
                      transition: 'all 0.1s',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.background = 'var(--po-hover)'; e.currentTarget.style.borderColor = 'var(--po-border-strong)'; }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'var(--po-panel-raised)'; e.currentTarget.style.borderColor = 'var(--po-border-strong)'; }}
                  >
                    {/* 左侧：名称 */}
                    <span style={{ fontSize: 14, color: 'var(--po-text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: 1, minWidth: 0 }}>
                      {resource.nodeName}
                    </span>

                    {/* 右侧：权限切换 + 删除 */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                      {/* Segmented Control: Read | Write */}
                      <div style={{
                        display: 'flex',
                        background: 'var(--po-panel)',
                        border: '1px solid var(--po-border)',
                        borderRadius: 4,
                        padding: 2,
                        gap: 1,
                      }}>
                        <button
                          onClick={() => updateDraftResource(resource.path, { readonly: true })}
                          style={{
                            background: isReadonly ? 'var(--po-border-strong)' : 'transparent',
                            border: 'none',
                            borderRadius: 3,
                            color: isReadonly ? 'var(--po-text)' : 'var(--po-text-disabled)',
                            cursor: 'pointer',
                            fontSize: 11,
                            height: 30,
                            padding: '0 8px',
                            fontWeight: 500,
                            transition: 'all 0.1s',
                          }}
                        >
                          Read
                        </button>
                        <button
                          onClick={() => updateDraftResource(resource.path, { readonly: false })}
                          style={{
                            background: !isReadonly ? 'color-mix(in srgb, var(--po-warning) 15%, transparent)' : 'transparent',
                            border: 'none',
                            borderRadius: 3,
                            color: !isReadonly ? 'var(--po-warning)' : 'var(--po-text-disabled)',
                            cursor: 'pointer',
                            fontSize: 11,
                            height: 30,
                            padding: '0 8px',
                            fontWeight: 500,
                            transition: 'all 0.1s',
                          }}
                        >
                          Write
                        </button>
                      </div>

                      <button
                        onClick={() => removeDraftResource(resource.path)}
                        style={{
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          width: 20, height: 20, borderRadius: 4,
                          background: 'transparent',
                          border: 'none',
                          color: 'var(--po-text-disabled)',
                          cursor: 'pointer',
                          transition: 'all 0.1s',
                        }}
                        onMouseEnter={e => { e.currentTarget.style.background = 'var(--po-border)'; e.currentTarget.style.color = 'var(--po-danger)'; }}
                        onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--po-text-disabled)'; }}
                      >
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <line x1="18" y1="6" x2="6" y2="18"></line>
                          <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* 拖拽提示 */}
            <div style={{
              minHeight: draftResources.length > 0 ? 32 : 88,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--po-text-disabled)',
            }}>
              <span style={{ fontSize: 12 }}>
                {draftResources.length > 0 ? 'Drag more' : 'Drag items into this'}
              </span>
            </div>
          </div>

          {/* Save Resources Button - 只在有更改时显示 */}
          {hasResourceChanges && (
            <button
              onClick={handleSaveResources}
              disabled={isSavingResources}
              style={{
                marginTop: 4,
                height: 30,
                padding: '0 12px',
                background: isSavingResources ? 'var(--po-border)' : 'var(--po-success)',
                border: 'none',
                borderRadius: 4,
                color: isSavingResources ? 'var(--po-text-disabled)' : 'var(--po-text-inverse)',
                fontSize: 12,
                fontWeight: 500,
                cursor: isSavingResources ? 'not-allowed' : 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 6,
                transition: 'all 0.15s',
              }}
            >
              {isSavingResources ? (
                <>
                  <Dots size="xs" />
                  Saving…
                </>
              ) : (
                <>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polyline points="20 6 9 17 4 12"></polyline>
                  </svg>
                  Save Changes
                </>
              )}
            </button>
          )}

          {/* Delete Button */}
          <button
            onClick={() => {
              if (confirm(`Delete "${currentAgent.name}"? This cannot be undone.`)) {
                deleteAgent(currentAgent.id);
              }
            }}
            style={{
              marginTop: 4,
              height: 30,
              padding: '0 10px',
              background: 'transparent',
              border: '1px solid var(--po-border)',
              borderRadius: 4,
              color: 'var(--po-text-disabled)',
              fontSize: 11,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 6,
              transition: 'all 0.15s',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.borderColor = 'var(--po-danger)';
              e.currentTarget.style.color = 'var(--po-danger)';
              e.currentTarget.style.background = 'color-mix(in srgb, var(--po-danger) 8%, transparent)';
            }}
            onMouseLeave={e => {
              e.currentTarget.style.borderColor = 'var(--po-border)';
              e.currentTarget.style.color = 'var(--po-text-disabled)';
              e.currentTarget.style.background = 'transparent';
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="3 6 5 6 21 6"></polyline>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
            </svg>
            Delete Access
          </button>
        </div>
      )}

      {/* The empty state and transcript share the same flexible region. */}
      <div className={`${chatStyles.conversation}${isEmpty ? ` ${chatStyles.emptyConversation}` : ''}`}>
        {isEmpty ? <AgentChatEmptyState /> : (
          <div className={chatStyles.transcript} role='log' aria-label='Chat messages' aria-busy={isLoading}>
            {messagesLoading ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 20, padding: '10px 0' }}>
                <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  <div
                    style={{
                      width: '100%',
                      height: 36,
                      borderRadius: 8,
                      background: 'var(--po-hover)',
                      position: 'relative',
                      overflow: 'hidden',
                    }}
                  >
                    <div className='skeleton-shimmer' />
                  </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {[90, 75, 60].map((w, i) => (
                    <div
                      key={i}
                      style={{
                        width: `${w}%`,
                        height: 14,
                        borderRadius: 4,
                        background: 'var(--po-hover)',
                        position: 'relative',
                        overflow: 'hidden',
                      }}
                    >
                      <div className='skeleton-shimmer' />
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((msg, idx) =>
                msg.role === 'user' ? (
                  <div key={msg.id || `user-${idx}`} className={chatStyles.message}>
                    <UserMessage message={{ content: msg.content, timestamp: msg.timestamp }} showAvatar={false} />
                  </div>
                ) : (
                  <div key={msg.id || `assistant-${idx}`} className={`${chatStyles.message} ${chatStyles.assistant}`}>
                    <BotMessage message={{ role: 'assistant', content: msg.content }} parts={msg.parts} isStreaming={msg.isStreaming} />
                  </div>
                )
              )
            )}
            <div ref={messagesEndRef} style={{ height: 1, flexShrink: 0 }} />
          </div>
        )}
      </div>

      {/* Input Area */}
      <ChatInputArea
        ref={inputAreaRef}
        inputValue={inputValue}
        onInputChange={handleInputChange}
        onKeyDown={handleKeyDown}
        onSend={handleSend}
        isLoading={isLoading}
        disabled={!currentAgentId}
        placeholder={messages.length ? 'Send follow-up' : 'Ask about this project'}
        showMentionMenu={mention.showMentionMenu}
        filteredMentionOptions={mention.filteredMentionOptions}
        mentionIndex={mention.mentionIndex}
        onMentionSelect={handleSelectMention}
        onMentionIndexChange={mention.setMentionIndex}
        onBlur={() => setTimeout(() => mention.closeMentionMenu(), 150)}
      />

      <style jsx global>{`
        @keyframes shimmer {
          0% {
            transform: translateX(-100%);
          }
          100% {
            transform: translateX(100%);
          }
        }
        .skeleton-shimmer {
          position: absolute;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: linear-gradient(
            90deg,
            transparent,
            var(--po-border),
            transparent
          );
          animation: shimmer 1.5s infinite;
        }
      `}</style>
    </div>
  );
}
