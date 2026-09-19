'use client';

import { useEffect, useState } from 'react';
import {
  accessPointProfileSlug,
  buildGitSyncPrompt,
  buildTerminalCliPrompt,
} from '@/lib/accessPointCliPrompt';
import {
  isGitRemoteProvider,
  isMcpProvider,
  isSandboxProvider,
} from '@/lib/accessProviderRegistry';
import { PanelShell } from '../PanelShell';
import { CountBadge } from '@/components/ui/CountBadge';
import { AccessPointProviderIcon, StatusDot } from './AccessPointProviderIcon';
import type { SyncEndpointInfo } from '../explorer';
import type { EndpointEntry, ProviderIconLookup } from './types';
import { ensureExpandedBatch } from '../explorer/explorerState';
import { canonicalGitUrlForTarget } from '@/lib/gitRemote';

function formatStatus(status: string) {
  if (!status) return 'Unknown';
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function formatDirection(direction: string) {
  if (direction === 'bidirectional') return 'Two-way sync';
  if (direction === 'inbound') return 'Syncs into workspace';
  if (direction === 'outbound') return 'Syncs from workspace';
  return direction || 'Access';
}

function getApiBase() {
  if (typeof window === 'undefined') return process.env.NEXT_PUBLIC_API_URL || '';
  return process.env.NEXT_PUBLIC_API_URL || window.location.origin;
}

function getSetupSnippets(ep: SyncEndpointInfo, displayName: string, scopeName: string) {
  const apiBase = getApiBase();
  const accessKey = ep.accessKey || '';

  if (isGitRemoteProvider(ep.provider) && ep.repositoryTarget) {
    const gitUrl = canonicalGitUrlForTarget(apiBase, ep.repositoryTarget);
    const profileName = accessPointProfileSlug(scopeName);
    const gitPrompt = buildGitSyncPrompt({
      gitUrl,
      scopeName,
      directoryName: scopeName,
      accessPointName: displayName,
    }).prompt;
    const terminalPrompt = accessKey
      ? buildTerminalCliPrompt({
          apiBase,
          accessKey,
          profileName,
          scopeName,
          accessPointName: displayName,
        }).prompt
      : '';
    return {
      primary: {
        title: 'Git Remote',
        description: 'Clone this scope with standard Git commands.',
        body: gitPrompt,
        copyText: gitPrompt,
      },
      secondary: terminalPrompt
        ? {
            title: 'Puppyone FS CLI',
            description: 'Use scoped FS CLI commands without a local clone.',
            body: terminalPrompt,
            copyText: terminalPrompt,
          }
        : undefined,
    } as const;
  }

  if (isMcpProvider(ep.provider) && accessKey) {
    const serverUrl = `${apiBase}/api/v1/mcp/proxy`;
    const serverName = displayName.toLowerCase().replace(/\s+/g, '-') || 'puppyone-mcp';
    const config = `{\n  "mcpServers": {\n    "${serverName}": {\n      "type": "http",\n      "url": "${serverUrl}",\n      "headers": { "Authorization": "Bearer ${accessKey}" }\n    }\n  }\n}`;
    const prompt = [
      `Configure this MCP Access Point for my coding agent.`,
      ``,
      `Access Point: ${displayName}`,
      `Scope: ${scopeName}`,
      `Server URL: ${serverUrl}`,
      `Header: Authorization: Bearer ${accessKey}`,
      ``,
      `Use this MCP config:`,
      config,
      ``,
      `After configuring it, use the MCP tools against the scoped Puppyone workspace data.`,
    ].join('\n');
    return {
      primary: {
        title: 'MCP',
        description: 'Configure this access point for an MCP-compatible client.',
        body: prompt,
        copyText: prompt,
      },
    };
  }

  if (isSandboxProvider(ep.provider) && accessKey) {
    const execUrl = `${apiBase}/api/v1/sandbox-endpoints/${ep.syncId}/exec`;
    const command = `curl -X POST ${execUrl} \\\n  -H "X-Access-Key: ${accessKey}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"command": "ls /workspace"}'`;
    const prompt = [
      `Use this Puppyone Sandbox Access Point to run commands in an isolated workspace environment.`,
      ``,
      `Access Point: ${displayName}`,
      `Scope: ${scopeName}`,
      `Exec URL: ${execUrl}`,
      `Access Key: ${accessKey}`,
      ``,
      `Example request:`,
      command,
      ``,
      `Use this sandbox endpoint for command execution related to the scoped workspace.`,
    ].join('\n');
    return {
      primary: {
        title: 'Sandbox',
        description: 'Run commands against this sandbox access point.',
        body: prompt,
        copyText: prompt,
      },
    };
  }

  if (ep.provider.startsWith('agent:')) {
    const prompt = [
      `Use this Puppyone agent Access Point from the scoped workspace.`,
      ``,
      `Access Point: ${displayName}`,
      `Scope: ${scopeName}`,
      `Agent ID: ${ep.syncId}`,
      ``,
      `Open the agent detail panel and use its available workspace resources for the task.`,
    ].join('\n');
    return {
      primary: {
        title: 'Agent',
        description: 'Use this agent access point from the scoped workspace.',
        body: prompt,
        copyText: prompt,
      },
    };
  }

  const prompt = [
    `Use this Puppyone Access Point.`,
    ``,
    `Access Point: ${displayName}`,
    `Scope: ${scopeName}`,
    `Endpoint ID: ${ep.syncId}`,
    accessKey ? `Access Key: ${accessKey}` : null,
    ``,
    `Use the detail view if you need provider-specific setup.`,
  ].filter(Boolean).join('\n');

  return {
    primary: {
      title: 'Access Point',
      description: 'Use this access point with provider-specific setup.',
      body: prompt,
      copyText: prompt,
    },
  };
}

function maskSecret(value: string) {
  if (!value) return 'Not issued';
  if (value.length <= 14) return value;
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

function getAncestorPaths(nodeId: string): string[] {
  const parts = nodeId.split('/').filter(Boolean);
  if (parts.length <= 1) return [];
  return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join('/'));
}

function InfoPill({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 5,
        minWidth: 0,
        padding: '3px 7px',
        borderRadius: 999,
        background: 'var(--po-hover)',
        border: '1px solid var(--po-hover)',
      }}
    >
      <span style={{ color: 'var(--po-text-subtle)', fontSize: 10, flexShrink: 0 }}>{label}</span>
      <span style={{ color: 'var(--po-text-muted)', fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {value}
      </span>
    </div>
  );
}

function CopyPromptButton({
  title,
  description,
  prompt,
  tone = 'neutral',
}: {
  title: string;
  description: string;
  prompt: string;
  tone?: 'green' | 'blue' | 'neutral';
}) {
  const [copied, setCopied] = useState(false);
  const color = tone === 'green' ? 'var(--po-success)' : tone === 'blue' ? 'var(--po-accent-text)' : 'var(--po-text-muted)';
  const border = tone === 'green'
    ? 'color-mix(in srgb, var(--po-success) 20%, transparent)'
    : tone === 'blue'
      ? 'color-mix(in srgb, var(--po-accent) 18%, transparent)'
      : 'var(--po-border)';
  const background = tone === 'green'
    ? 'color-mix(in srgb, var(--po-success) 7%, transparent)'
    : tone === 'blue'
      ? 'color-mix(in srgb, var(--po-accent) 7%, transparent)'
      : 'var(--po-hover)';
  return (
    <button
      type="button"
      onClick={async () => {
        await navigator.clipboard.writeText(prompt);
        setCopied(true);
        setTimeout(() => setCopied(false), 1800);
      }}
      style={{
        width: '100%',
        textAlign: 'left',
        borderRadius: 8,
        border: `1px solid ${copied ? 'color-mix(in srgb, var(--po-success) 38%, transparent)' : border}`,
        background: copied ? 'color-mix(in srgb, var(--po-success) 10%, transparent)' : background,
        minHeight: 56,
        padding: '10px 12px',
        transition: 'border-color 0.2s',
        cursor: 'pointer',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ color, fontSize: 12, fontWeight: 600, lineHeight: 1.35 }}>{title}</div>
          <div style={{ color: 'var(--po-text-subtle)', fontSize: 10, lineHeight: 1.45, marginTop: 2 }}>{description}</div>
        </div>
        <span style={{
          flexShrink: 0,
          color: copied ? 'var(--po-success)' : 'var(--po-text-muted)',
          fontSize: 10,
          fontWeight: 500,
          border: `1px solid ${copied ? 'color-mix(in srgb, var(--po-success) 24%, transparent)' : 'var(--po-border)'}`,
          borderRadius: 999,
          padding: '4px 8px',
          background: copied ? 'color-mix(in srgb, var(--po-success) 10%, transparent)' : 'var(--po-hover)',
        }}>
          {copied ? 'Copied' : 'Copy Prompt'}
        </span>
      </div>
    </button>
  );
}

export function AccessPointsListPanel({
  projectId,
  entries,
  providerIcons,
  expandedEndpointId,
  onClose,
  onEndpointClick,
  onEndpointHover,
}: {
  projectId: string;
  entries: EndpointEntry[];
  providerIcons: ProviderIconLookup;
  expandedEndpointId?: string | null;
  onClose: () => void;
  onEndpointClick: (ep: SyncEndpointInfo, nodeId: string) => void;
  onEndpointHover?: (nodeId: string | null) => void;
}) {
  const [hoveredEndpoint, setHoveredEndpoint] = useState<string | null>(null);
  const [expandedEndpoint, setExpandedEndpoint] = useState<string | null>(expandedEndpointId ?? null);

  useEffect(() => {
    if (expandedEndpointId) setExpandedEndpoint(expandedEndpointId);
  }, [expandedEndpointId]);

  return (
    <PanelShell
      title="Access Points"
      onClose={onClose}
      headerRight={
        <CountBadge
          value={entries.length}
          size="md"
          tone="neutral"
        />
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: 'var(--po-canvas)' }}>
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '12px 12px 24px', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {entries.length === 0 ? (
            <div style={{ padding: '32px 16px', textAlign: 'center', color: 'var(--po-text-subtle)', fontSize: 12, lineHeight: 1.6 }}>
              Access points created from folder link buttons will appear here.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {entries.map(({ ep, nodeId, name, nodeName }) => {
                const hovered = hoveredEndpoint === ep.syncId;
                const expanded = expandedEndpoint === ep.syncId;
                const scopeName = nodeName || (nodeId ? nodeId : 'Root');
                const setup = getSetupSnippets(ep, name, scopeName);
                return (
                  <div
                    key={`access-panel-${ep.syncId}`}
                    onMouseEnter={() => {
                      setHoveredEndpoint(ep.syncId);
                      ensureExpandedBatch(projectId, getAncestorPaths(nodeId));
                      onEndpointHover?.(nodeId);
                    }}
                    onMouseLeave={() => {
                      setHoveredEndpoint(null);
                      onEndpointHover?.(null);
                    }}
                    style={{
                      width: '100%',
                      borderRadius: 8,
                      border: '1px solid',
                      borderColor: expanded || hovered ? 'var(--po-border-strong)' : 'var(--po-border-subtle)',
                      background: expanded ? 'var(--po-hover)' : hovered ? 'var(--po-border-subtle)' : 'var(--po-panel)',
                      textAlign: 'left',
                      transition: 'all 0.15s',
                      overflow: 'hidden',
                    }}
                  >
                    <button
                      type="button"
                      onClick={() => setExpandedEndpoint(expanded ? null : ep.syncId)}
                      style={{
                      display: 'flex',
                      alignItems: 'center',
                        gap: 10,
                        width: '100%',
                        padding: '8px 10px',
                        border: 'none',
                        background: 'transparent',
                        color: 'inherit',
                        cursor: 'pointer',
                        textAlign: 'left',
                        overflow: 'hidden',
                      }}>
                      <div style={{
                        width: 32,
                        height: 32,
                        borderRadius: 8,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        position: 'relative',
                        background: 'transparent',
                        flexShrink: 0,
                      }}>
                        <AccessPointProviderIcon ep={ep} providerIcons={providerIcons} />
                        <StatusDot status={ep.status} />
                      </div>
                      <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
                        <span style={{
                          fontSize: 12,
                          fontWeight: 500,
                          lineHeight: 1.3,
                          color: hovered || expanded ? 'var(--po-text)' : 'var(--po-text)',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          transition: 'color 0.15s',
                        }}>
                          {name}
                        </span>
                        <span style={{
                          marginTop: 1,
                          fontSize: 12,
                          lineHeight: 1.3,
                          color: hovered ? 'var(--po-success)' : 'var(--po-text-subtle)',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}>
                          Scope: {scopeName}
                        </span>
                      </span>
                      <div style={{ color: hovered || expanded ? 'var(--po-text-subtle)' : 'var(--po-text-disabled)', transition: 'color 0.15s, transform 0.15s', flexShrink: 0, transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)' }}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="9 18 15 12 9 6" />
                        </svg>
                      </div>
                    </button>

                    {expanded && (
                      <div style={{ padding: '0 10px 10px 52px', display: 'flex', flexDirection: 'column', gap: 12 }}>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, paddingTop: 2 }}>
                          <InfoPill label="Status" value={formatStatus(ep.status)} />
                          <InfoPill label="Scope" value={scopeName} />
                          <InfoPill label="Mode" value={formatDirection(ep.direction)} />
                          <InfoPill label="Key" value={maskSecret(ep.accessKey || ep.syncId)} />
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                          <div style={{ color: 'var(--po-text-subtle)', fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                            Copy Prompt
                          </div>
                          <CopyPromptButton
                            title={setup.primary.title}
                            description={setup.primary.description}
                            prompt={setup.primary.body}
                            tone={isGitRemoteProvider(ep.provider) ? 'green' : 'neutral'}
                          />
                          {setup.secondary && (
                            <CopyPromptButton
                              title={setup.secondary.title}
                              description={setup.secondary.description}
                              prompt={setup.secondary.body}
                              tone="blue"
                            />
                          )}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <button
                            type="button"
                            onClick={() => onEndpointClick(ep, nodeId)}
                            style={{
                              height: 30,
                              padding: '0 10px',
                              borderRadius: 6,
                              border: '1px solid var(--po-active)',
                              background: 'var(--po-control)',
                              color: 'var(--po-text)',
                              fontSize: 12,
                              fontWeight: 500,
                              cursor: 'pointer',
                            }}
                          >
                            View details
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </PanelShell>
  );
}
