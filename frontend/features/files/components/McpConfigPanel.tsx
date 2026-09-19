'use client';

import { PageLoading } from '@/components/loading';
import { StatusDot } from '@/components/ui/StatusDot';
import { PanelShell } from '@/features/files/components/PanelShell';
import React from 'react';

interface McpEndpointData {
  id: string;
  name: string;
  api_key: string;
  api_key_hint?: string;
  api_key_revealed?: boolean;
  /** Canonical server URL from the backend; preferred over re-deriving it. */
  server_url?: string;
  status: string;
  accesses: { path: string; json_path: string; readonly: boolean }[];
}

interface McpConfigPanelProps {
  endpoint: McpEndpointData | null | undefined;
  onClose: () => void;
  onBack?: () => void;
}

const McpIcon = (
  <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="var(--po-accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="3" width="20" height="14" rx="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
  </svg>
);

const FolderIcon = (
  <img src="/icons/folder.svg" alt="Folder" width={36} height={36} style={{ display: 'block' }} />
);

function ConnectionArrow() {
  return (
    <svg width="48" height="16" viewBox="0 0 48 16" fill="none" stroke="var(--po-success)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 5h44M6 2L2 5l4 3" />
      <path d="M42 8l4 3-4 3M2 11h44" />
    </svg>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--po-text-subtle)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 6 }}>
      {children}
    </div>
  );
}

function CodeBlock({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 12, color: 'var(--po-text-muted)', background: 'var(--po-control)', border: '1px solid var(--po-border)', borderRadius: 6, padding: '8px 10px', wordBreak: 'break-all', fontFamily: 'var(--po-font-sans)' }}>
      {children}
    </div>
  );
}

export function McpConfigPanel({ endpoint, onClose, onBack }: McpConfigPanelProps) {
  if (!endpoint) {
    return (
      <PanelShell title="MCP Endpoint" icon={McpIcon} onClose={onClose} onBack={onBack}>
        <PageLoading variant="fill" />
      </PanelShell>
    );
  }

  // The /api/v1/mcp/proxy/* path is served by the backend, so the URL
  // we hand to MCP clients must point at the backend host, not the
  // Next.js frontend origin. NEXT_PUBLIC_API_URL is the canonical
  // backend URL set at build time.
  const apiBase = typeof window !== 'undefined'
    ? (process.env.NEXT_PUBLIC_API_URL || window.location.origin)
    : '';
  // Prefer the backend-issued canonical URL; fall back to deriving it.
  const serverUrl = endpoint.server_url || `${apiBase}/api/v1/mcp/proxy`;
  const targetLabel = endpoint.accesses.length > 0
    ? endpoint.accesses[0].path
    : 'Workspace';

  return (
    <PanelShell title={endpoint.name} icon={McpIcon} onClose={onClose} onBack={onBack}>
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
        {/* Connection visualization - keep style aligned with Sync detail panel */}
        <div style={{ borderRadius: 10, padding: '16px 12px 12px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, width: 88 }}>
              <div style={{
                width: 36, height: 36, borderRadius: 8,
                background: 'var(--po-panel-raised)', border: '1px solid var(--po-border)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="var(--po-accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="2" y="3" width="20" height="14" rx="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
                </svg>
              </div>
              <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--po-text-muted)', textAlign: 'center' }}>
                MCP Server
              </div>
            </div>

            <div style={{ flex: 1, display: 'flex', justifyContent: 'center' }}>
              <ConnectionArrow />
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, width: 88 }}>
              {FolderIcon}
              <div style={{
                fontSize: 11, fontWeight: 500, color: 'var(--po-text-muted)', textAlign: 'center',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 88,
              }}>
                {targetLabel}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <StatusDot tone={endpoint.status === 'active' ? 'success' : 'warning'} />
            <span style={{ fontSize: 11, color: 'var(--po-text-muted)' }}>{endpoint.status}</span>
          </div>
        </div>

        <div>
          <SectionLabel>Server URL</SectionLabel>
          <CodeBlock>{serverUrl}</CodeBlock>
        </div>
        <div>
          <SectionLabel>API Key</SectionLabel>
          <CodeBlock>{endpoint.api_key || endpoint.api_key_hint || 'Regenerate the key to reveal it once.'}</CodeBlock>
        </div>
        <div>
          <SectionLabel>Cursor Config</SectionLabel>
          <pre style={{ fontSize: 11, color: 'var(--po-text-muted)', background: 'var(--po-control)', border: '1px solid var(--po-border)', borderRadius: 6, padding: '8px 10px', margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'var(--po-font-sans)' }}>
{JSON.stringify({
  mcpServers: {
    [endpoint.name.toLowerCase().replace(/\s+/g, '-')]: {
      type: 'http',
      url: serverUrl,
      headers: { Authorization: `Bearer ${endpoint.api_key || '<rotate-key-to-reveal>'}` },
    }
  }
}, null, 2)}
          </pre>
        </div>
        {endpoint.accesses.length > 0 && (
          <div>
            <SectionLabel>Accesses ({endpoint.accesses.length})</SectionLabel>
            {endpoint.accesses.map((a, i) => (
              <div key={i} style={{ fontSize: 12, color: 'var(--po-text-muted)', padding: '4px 0' }}>
                {a.path} {a.readonly ? '(read-only)' : '(read-write)'}
              </div>
            ))}
          </div>
        )}
      </div>
    </PanelShell>
  );
}
